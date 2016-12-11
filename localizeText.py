#!/usr/bin/python

"""
This script is part of a concept for effectively managing text localization for iOS apps. 
The basic idea is that the localizable texts and their translations should be managed by an RDBMS 
so we can rationalize the translation and testing (of localized strings).

For iOS platform, extraction of localizable strings from source code is accomplished using the genstrings 
line command which produces a master string file. This master file can be reformatted to a .csv file
so it can be imported into an RDBMS table. It may also be reformatted for submission to an online
translation API service such as gcloud Translation API. 

Action "GenCsvFromAppStrings" converts each item from the file tree into a .csv record. 
If the option "for_all_langs" is set, the localizable.string from all the
language subfolders are included in the .csv. This is useful to upload items which may have been
translated manually.

Action "UploadToDb" upserts the .csv entries into Oracle DB tables

Action "TranslateViaGcloud" takes an .csv file which contains strings from the master language, 
another .csv which contains the target languages, compose a json file to send to gcloud and 
stores the translation output to another .csv file. 

In the case of gcloud, the json output from the API will be either:
1. transformed to a .csv with the translated items of all target languages.
2. optionally, reformatted to the zh.CN/localizable.strings, de.DE/localizable.strings directly. 
To reduce coding effort initially, we implement only gcloud.json to .csv conversion.

Action "DownloadAppStringFromDb" exports items of master and all target languages 
into a .csv file.

Action "CsvToIosFileSet" converts items to a file set as expected by XCode.

A typical work flow will be:

1) Given the app folder, call genstrings to generate the master language localizable.strings 
(possibly also info.plist) file and convert the stuff to a .csv file. The method will also
compile the list of target languages the app is configured to support
2) convert the .csv file to json request file, with the intended target languages
3) convert the output to a .csv file for upload
4) THIS STEP CAN BE SKIPPED INITIALLY: upload the stuff / download back to .csv ( the storage in the database is for deduplication
and other analytical purpose, but also for building up a vocabulary and phrase database)
5) convert the .csv file containing all translated items to iOS file set
6) deploy the file set

Example for json processing:
json file has data:
{
    "maps": [
        {
            "id": "blabla",
            "iscategorical": "0"
        },
        {
            "id": "blabla",
            "iscategorical": "0"
        }
    ],
    "masks": {
        "id": "valore"
    },
    "om_points": "value",
    "parameters": {
        "id": "valore"
    }
}

Code:

import json
from pprint import pprint

with open('data.json') as data_file:    
    data = json.load(data_file)

pprint(data) 

# With data, find values like so (it is a dictionary, where the value may be a scalar, an array or again a dictionary ):
# data is a dictionary
# -> the key "maps" has an array of dictionary as value
# -> -> since the first array element is again a dictionary, we can find a key "id" 

data["maps"][0]["id"]
data["masks"]["id"]
data["om_points"]
"""

import argparse
import codecs # for reading files in Unicode 
import getopt
import os
import re
import shutil # to copy files
import sys
import tempfile
import time

from sets import Set

cmd_ln_options= {}
cmd_ln_options ['source_dir']= None # mandatory 
cmd_ln_options ['output_file']= '/tmp/all_localizable_strings.txt' 
cmd_ln_options ['debug']= True

def debug(s):
	if cmd_ln_options ['debug'] == True:
		print("DBX:%s: %s" % (time.strftime('%X'), s) )
def info(s):  print("*INFO* %s: %s" % (time.strftime('%X'), s) )
def error(s):  sys.stderr.write("!! ERROR !! => %s\n" % s)

def parseCmdLine() :

	global g_ConnectString
	global g_OraUser

	parser = argparse.ArgumentParser()
	# lowercase shortkeys
	parser.add_argument( '-a', '--action', help='which action applies', choices=['GenCsvFromAppStrings', 'UploadCsvToDb', 'DownloadAppStringFromDb', 'TranslateViaGcloud', 'DeployCsvToAppFolder' ], required= True)
	parser.add_argument( '-c', '--connect_string', help='Oracle connect string' )
	parser.add_argument( '-n', '--app_name')
	parser.add_argument( '-o', '--ora_user')
	parser.add_argument( '-O', '--output_file')
	parser.add_argument( '-t', '--target_table', default='M_APP_LOCALIZABLE_STRING' )
	parser.add_argument( '-x', '--xcode_project_folder')
	# long keywords only

	result= parser.parse_args()

	# for (k, v) in vars( result ).iteritems () : print( "%s : %s" % (k, v) )
	if result.connect_string != None: 
		g_ConnectString=  result.connect_string
	if result.ora_user != None: g_OraUser=  result.ora_user

	return result

#################################################################################
def globLocalizableFileSet (p_file_tree):
	retval_files= []
	retval_parent_folders= []
	root_path_strlen= len(p_file_tree)
	# deal with trailing path separator
	if p_file_tree[-1] == os.path.sep: root_path_strlen-= 1

	for cur_root, sub_dirs, files in os.walk ( p_file_tree ):
		for file_node in files:
			# debug("file_node: %s" % file_node)
			file_prefix, file_ext= os.path.splitext( file_node )
			# debug("file_ext: %s" % file_ext)
			# list relevant file_node extensions here 	
			# note that InfoPlist.strngs has another encoding than Localizable.strings! 
			if file_ext in ( '.strings') and file_ext != '' and file_node != 'InfoPlist.strings' :
				#debug("cur_root: %s" % cur_root)
				retval_parent_folders.append( cur_root )
				rel_path= cur_root[root_path_strlen+1: ] 
				# return the "relative" source dir
				retval_files.append( os.path.join(rel_path, file_node) ) 
	return (retval_parent_folders, retval_files)


#################################################################################
def getLangFromFolderName (p_folder_name):
	bn = os.path.basename(p_folder_name)
	tokens= bn.split('.')
	assert len(tokens) == 2, 'folder name strings file does not have exactly 2 dot separated components!'
	retval_lang= tokens[0]
	debug("retval_lang : %s" % retval_lang )
	return retval_lang

#################################################################################
def parseLocalizableItem (p_record):
	comment_start= p_record.find('/*')
	comment_end= p_record.find('*/')
	# debug("comment found between %d and %d" % (comment_start, comment_end ) )
	comment= None
	key_name= None
	key_value= None
	# extract comment which comes as first field 
	if comment_start >=0 and comment_end > comment_start:
		#debug("Record len: %d" % len(p_record) )
		#debug("Record: %s" % p_record)

		comment= p_record[ comment_start+2: comment_end]
		#debug("comment starts with: %s ..." % comment[0: 50] )

		# extract key
		key_start= p_record.find('\"', comment_end)
		if key_start < 0: debug("bad record: %s" % p_record)
		assert key_start >= 0, "Starting quote for key not found!"

		key_end= p_record.find('\"', key_start+1)
		#debug("key_end: %d" % key_end )
		if key_end < 0: debug("bad record: %s" % p_record)
		assert key_end >= key_start, "Ending quote for key not found!"
		key_name = p_record[ key_start+1: key_end]
		# debug("key_start, key_end: %d/%d" % (key_start, key_end) )
		# debug("key_name: %s" % key_name )

		# extract value
		value_start= p_record.find('\"', key_end+1)
		if value_start < 0: debug("bad record: %s" % p_record)
		assert value_start >= 0, "Starting quote for value not found!"
		value_end= p_record.find('\"', value_start+1)
		if value_end < 0: debug("bad record: %s" % p_record)
		assert value_end >= value_start, "Ending quote for value not found!"
		# debug("value_start, value_end: %d/%d" % (value_start, value_end) )
		key_value = p_record[ value_start+1: value_end]
		# debug("key_value: %s" % key_value )
	#
	return key_name, key_value, comment

#################################################################################
def quote (p_str):
	return "\"%s\"" % (p_str)

#################################################################################
def processIosLocalizableFile (p_source_file, p_target_handle, p_language, p_territory, p_is_master):
	# 
	# fixme: we should detect encoding automatically! 
	fh= codecs.open( p_source_file, 'r', encoding='utf-16')
	# fh= codecs.open( p_source_file, 'r', encoding='utf-8')
	file_text= fh.read()
	# debug("length of file text: %d" % file_text.__len__() )

	# detect end_of_line style
	found_dos_eol = file_text.find( '\r\n' );
	if found_dos_eol > 0:
		records= file_text.split(';\r\n');
	else:
		records= file_text.split(';\n');

	debug("number of records: %d" % len( records ) )

	for record in records:
		record= record.replace('\n',';')
		key, value, comment= parseLocalizableItem( record)
		
	# p_target_handle.write("\n... Content of file \"%s\"\n\n" % (p_source_file) )
		if key != None:
			my_array= ( # start of scalar which is actually a list
				quote(p_language), quote(p_territory) 
				,quote(key)
				,quote(value)
				,quote(comment)
				)# start of scalar which is actually a list
			output_line= "<;>".join( my_array )
			p_target_handle.write( "%s\n" %output_line )
		#debug("encoding of line: %s" % type(line) )
		

#################################################################################
def appendTextFileToFileHandle (p_source_file, p_target_handle):
	fh= open( p_source_file, 'r')
	for line in fh.readlines():
		p_target_handle.write(line)
      
def testOracleConnect( oraUser, oraPassword, connectString ) :
	connectCommand=  composeConnectCommand( oraUser, oraPassword, connectString ) 
	# _dbx( connectCommand )
	proc= subprocess.Popen( ['sqlplus', '-s', '/nolog'] ,stdin=subprocess.PIPE ,stdout=subprocess.PIPE ,stderr=subprocess.PIPE)
	msgLines, errLines= proc.communicate( connectCommand )
	if len( msgLines ) > 0 or len( errLines ) > 0 :
		print( sys.stderr, ''.join( msgLines ) )
		print( sys.stderr, ''.join( errLines  ) )

		_errorExit( "Oracle test connect on %s@%s failed! Check the credentials" % ( oraUser, connectString ) )      

#################################################################################
def convertCsvToGcloudJson ( masterCsvPath, targetLangs ):
	"""
	"""
	return jsonPath

#################################################################################
def convertGcloudJsonOutputToCsv ( jsonPath ):
	"""
	"""
	return csvPath

#################################################################################
def convertCsvToIosFileTree ( jsonPath ):
	"""
	"""
	return csvPath

#################################################################################
def actionGenCsvFromAppStrings( appFolderPath, outputFile , forAllLang = False ):
	"""The encoding of the input file is currently hardcoded! Look for codecs
This script select all the files named "Localizable.string" under the current file tree and perform the following operations
	* Remember the folder name of the selected file - obviously the file only exists once in each folder.
	* One of the string file is the master. By default it is under the en.lproj folder
	* Assemble one record from several lines of the input file. Each record has this fields:
		** Key
		** Langulage which is derived from the containing folder
		** Territory which is derived from the containing folder
		** Localized version. For the master file, it is identical to the key
		** Translator hints, but only for the master file
	
All the records are output to a file. This file can be loaded into an RDBMS so we can see if there are translated text for the keys and how each key is translated.

As keys are added or deleted, we simply merge (with delete option) the keys into the database, tag these keys with the master language. We can then either directly add the translations into the database or output a string file for each target language into the standard file format and have them translated. This script can be re-used to upload the translations.

If the Localizable.strings file is in utf-8 format, convert it to utf16 with BOM to make this python script happy by the following line commands:

mv Localizable.strings Localizable.strings.org; iconv -f utf-8 -t UTF-16 Localizable.strings.org > Localizable.strings
	"""
	info("Concat target file is %s" % outputFile)
	out_fh = codecs.open( outputFile, "w", encoding='utf-16' )
	for ix in range (len ( localizableFiles ) ):
		source_path_complete= os.path.join(source_root, localizableFiles[ix] )
		debug("source_path_complete: %s" % source_path_complete)
		lang_code= getLangFromFolderName( strings_file_folders[ix] )
		
		# out_fh.write("\n... Content of file \"%s\"\n\n" % (source_path_complete) )
		processIosLocalizableFile (p_source_file= source_path_complete, p_target_handle= out_fh, p_language= lang_code, p_territory=None, p_is_master=1)
		# appendTextFileToFileHandle (p_source_file=source_path_complete , p_target_handle= out_fh)
	out_fh.close()

#################################################################################
def actionUploadCsvToDb ( csvPath, targetSchema, targetTable ):
	"""
	"""

#################################################################################
def actionDownloadAppStringFromDb ( appKey, forAllLangs = True ):
	"""
	"""
	return csvPath

#################################################################################
def actionTranslateViaGcloud ( masterCsvPath, targetLangs ):
	"""
	"""

#################################################################################
def actionDeployCsvToAppFolder ( allLangCsvPath, appFolderPath ):
	"""
	"""

#################################################################################
def main():
	argObject= parseCmdLine()
	
	sel_files= ()
	if argObject.action == 'generate_csv_from_ios':
		actionGenCsvFromAppStrings( outputFile = argObject.outputFile )
	else:
		_errorExit( "Action %s is not yet implemented" % ( argObject.action ) )
		
	source_root= cmd_ln_options["source_dir"] 
	# deal with trailing path separator
	if source_root [-1] == os.path.sep: source_root= source_root[0: -1]
	if cmd_ln_options["source_dir"] == None : 
		print_help_and_exit()

	info( "action: %s" %cmd_ln_options["action"] )
	strings_file_folders, localizableFiles= globLocalizableFileSet( p_file_tree= source_root )
		

if __name__ == "__main__":
	main()


#!/usr/bin/python
# coding=utf-8
"""
This script is part of a concept for effectively managing text localization for iOS apps. 
The basic idea is that the localizable texts and their translations should be managed by an RDBMS 
so we can rationalize the translation and testing (of localized strings).

For iOS platform, extraction of localizable strings from source code is accomplished using the genstrings 
line command which produces a master string file. This master file can be reformatted to a .csv file
so it can be imported into an RDBMS table. It may also be reformatted for submission to an online
translation API service such as gcloud Translation API. 

To review: Action "GenCsvFromAppStrings" converts each item from the file tree into a .csv record. 
If the option "for_all_langs" is set, the localizable.string from all the
language subfolders are included in the .csv. This is useful to upload items which may have been
translated manually.

Action "UploadToDb" upserts the .csv entries into Oracle DB tables

Action "TranslateAppStringsFileViaGcloud" takes the master app strings file, generates json request files
as many as needed, calls translator, converts the result to iOS format

Action "LocalizeAppViaGcloud" builds on TranslateAppStringsFileViaGclou and adds the following steps:
	Pre-processing: takes as argument the path to the project folder, generates a fresh Localizable.strings
	Call TranslateAppStringsFileViaGcloud
	Post-processing: 
		*converts the Localizable.strings files from UTF-16 to 8 so we can run "diff -u" on it (Apple seems to flavour UTF-16, although UTF-8 seems to work). For a quick win we may generate UTF-8 from the beginning. Conversion to UTF-16 can be done later.
		*builds a diff-report as output for review
Action DeployCsvToAppFolder takes a tree path containing the localized strings files and overwrite the existing files.
		
Action "deploy" builds on TranslateAppStringsFileViaGclou and adds the following steps:

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
import glob
import inspect
import json
import os
import pprint
import re
import shutil # to copy files
import subprocess
import sys
import tempfile
import time

from sets import Set

g_authToken= None
g_gTokenEnvVarName='GCLOUD_TOKEN'

g_defaultAppStringsFile= 'Localizable.strings'
g_defaultGcloudRequestFile= 'translateRequest.json'
g_defaultTargetLangs = ['zh', 'it' ]
g_supportedStringFiles =	[ 'Localizable.strings', 'Table.strings' ]
g_dbxCnt = 0
g_maxDbxMsg = 5000

g_homeDir = os.environ["HOME"]

def _dbx ( text ):
	global g_dbxCnt
	print( 'dbx: %s - Ln%d: %s' % ( inspect.stack()[1][3], inspect.stack()[1][2], text ) )
	g_dbxCnt += 1
	if g_dbxCnt > g_maxDbxMsg:
		_errorExit( "g_maxDbxMsg of %d exceeded" % g_maxDbxMsg )

def _infoTs ( text , withTs = False ):
	if withTs :
		print( '%s (Ln%d) *** %s' % ( time.strftime("%H:%M:%S"), inspect.stack()[1][2], text ) )
	else :
		print( '(Ln%d) *** %s' % ( inspect.stack()[1][2], text ) )

def _printStdErr ( text ):
		sys.stderr.write( text + "\n" )

def _errorExit ( text ):
	print( 'ERROR raised from %s - Ln%d: %s' % ( inspect.stack()[1][3], inspect.stack()[1][2], text ) )
	sys.exit(1)

def parseCmdLine() :

	global g_ConnectString
	global g_OraUser

	parser = argparse.ArgumentParser()
	# lowercase shortkeys
	parser.add_argument( '-a', '--action', help='which action applies'
		, choices=[ 'DeployIosFilesToAppProject' , 'DownloadAppStringFromDb', 'GenCsvFromAppStrings', 'LocalizeAppViaGcloud' , 'TranslateAppStringsFileViaGcloud', 'UploadCsvToDb', 'SpecialTest' ],
 required= True)
	parser.add_argument( '-c', '--connectString', help='Oracle connect string' )
	parser.add_argument( '-f', '--deployFrom', help='parent of the temporary lproj folders' )
	parser.add_argument( '-n', '--appName')
	parser.add_argument( '-o', '--oraUser')
	parser.add_argument( '-O', '--outputCsv')
	parser.add_argument( '-t', '--targetTable', default='M_APP_LOCALIZABLE_STRING' )
	parser.add_argument( '-x', '--xcodeProjectFolder')
	# long keywords only
	parser.add_argument( '--appStringsFile', help= 'localizable strings file such as created by genstrings', default = g_defaultAppStringsFile )
	parser.add_argument( '--jsonRequestFile', help= 'input/output json request file path, depending on action', default= g_defaultGcloudRequestFile )

	result= parser.parse_args()

	# for (k, v) in vars( result ).iteritems () : print( "%s : %s" % (k, v) )
	if result.connectString != None: 
		g_ConnectString=  result.connectString
	if result.oraUser != None: g_OraUser=  result.oraUser

	action = result.action
	if False: # pseudo head of "switch" statement
		None
	elif action == 'DeployIosFilesToAppProject' :
		if result.xcodeProjectFolder  ==None: _errorExit( "Parameter %s is required for %s" % ( 'xcodeProjectFolder', action ) )
		if result.deployFrom == None: _errorExit( "Parameter %s is required for %s" % ( 'deployFrom', action ) )
	elif action == 'GenCsvFromAppStrings' :
		if result.outputCsv == None: _errorExit( "Parameter %s is required for %s" % ( 'outputCsv', action ) )
		if result.xcodeProjectFolder == None: _errorExit( "Parameter %s is required for %s" % ( 'xcodeProjectFolder', action ) )
	elif action == 'LocalizeAppViaGcloud' :
		if result.xcodeProjectFolder == None: _errorExit( "Parameter %s is required for %s" % ( 'xcodeProjectFolder', action ) )
	elif action == 'TranslateAppStringsFileViaGcloud' :
		if result.jsonRequestFile == None: _errorExit( "Parameter %s is required for %s" % ( 'jsonRequestFile', action ) )
	elif action == 'UploadCsvToDb' :
		None
 
	return result

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
def singleQuote (text):
	return "'%s'" % (text)

#################################################################################
def quote (text):
	return "\"%s\"" % (text)

#################################################################################
def processIosLocalizableFile (p_source_file, p_target_handle, p_language, p_territory, p_is_master):
	# 
	# fixme: we should detect encoding automatically! 
	fh= codecs.open( p_source_file, 'r', encoding='utf-16')
	# fh= codecs.open( p_source_file, 'r', encoding='utf-8')
	file_text= fh.read()

	# detect end_of_line style
	found_dos_eol = file_text.find( '\r\n' );
	if found_dos_eol > 0:
		records= file_text.split(';\r\n');
	else:
		records= file_text.split(';\n');

	_dbx("number of records: %d" % len( records ) )

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
		#_dbx ("encoding of line: %s" % type(line) )
		
#################################################################################
def parseAppStringsFile ( sourceFile ):
	_dbx( sourceFile )
	# 
	# fixme: we should detect encoding automatically! 
	fh= codecs.open( sourceFile, 'r', encoding='utf-16')
	# fh= codecs.open( sourceFile, 'r', encoding='utf-8')
	fileText= fh.read()

	# detect end_of_line style
	foundDosEol = fileText.find( '\r\n' );
	if foundDosEol > 0:
		records= fileText.split(';\r\n');
	else:
		records= fileText.split(';\n');

	_dbx( "number of records: %d" % len( records ) )

	# note that for the master localizable file, translation key and gui text are identical!
	translationKeys = []
	guiTexts = []
	comments = []
	for record in records:
		record= record.replace('\n',';')
		translationKey, guiText, comment= parseLocalizableItem( record )
		if translationKey != None:
			translationKeys.append( translationKey )
			guiTexts.append( guiText )
			comments.append( comment )
	_dbx( "keys: %d" % len( translationKeys ) )

	return translationKeys, guiTexts, comments

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
		_printStdErr( ''.join( msgLines ) )
		_printStdErr( ''.join( errLines  ) )

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
def grepRelevantSourceFiles ( appFolder ):
	"""
	"""
	paths = []

	for dirPath, subdirs, fileNodes in os.walk ( appFolder ):
		for fileNode in fileNodes:
			namePrefix, nameSuffix= os.path.splitext( fileNode )
			# list relevant fileNode extensions here 	
			if nameSuffix in ( 'swift', 'm'):
				_dbx( "fileNode: %s" % fileNode )
				_dbx( "dirPath: %s" % dirPath )
				retval_files.append( os.path.join(dirPath, fileNode) ) 
	return paths

#################################################################################
def convertTranslationOutputToIosFormat ( targetLang, translationKeys, comments, formattersList, translationResultPath, iosFilePath ) :
	"""
given a gcloud translation output file, which looks like this:

{ "data": {
    "translations": [
      { "translatedText": "{0} Jahr (e) {1} Monate (n) {2} ay (n)" },
      { "translatedText": "abgebrochen bei" }
    ]
  }
}

convert it and store to the given iOS file path.
Note that we need to convert the placeholders {n\} back to its original %d or %s and the like
	"""

	_dbx( translationResultPath )
	inputFh= open(translationResultPath, 'r') 
	jsonData= json.load( inputFh )
	# pprint.pprint( jsonData )
	dataRoot= jsonData["data"]
	translations= dataRoot["translations"]

	iosRecords= []
	placeholderPat = re.compile( r'{\d}' )
	for ixText, d in enumerate( translations ):
		iosRecord= {}
		iosRecord['key']= translationKeys[ixText] 
		iosRecord['comment']= comments[ixText] 
		# 
		text = d["translatedText"]
		# _dbx( text )
		parts= placeholderPat.split( text )
		formatters= formattersList[ixText] 
		if len( formatters ) != len( parts ) - 1 : # nature of split: delimiter is between 2 tokens
			_infoTs( len( formatters ) )
			_infoTs( len( parts ) )
			_infoTs( "Text:\n%s" % text )
			_infoTs( "Formatters:\n%s" % ",".join( formatters ) )
			_errorExit( "Trouble: Number of formatters does not agree with text!" )
		if len( formatters ) > 0:
			# _dbx( ','.join( formatters ) )
			newText= ''
			for ixPart, part in enumerate( parts ):
				if ixPart < len( formatters ):
					newText += part + formatters[ixPart] 
				else:
					newText += part 
			iosRecord['text']= newText
		else:
			iosRecord['text']= text
			
		iosRecords.append( iosRecord )
	# _dbx("*"*40 + "ios Records");	pprint.pprint( iosRecords )
	lines= []
	for rec in iosRecords:
		lines.append( "/* %s */" % rec["comment"] )
		lines.append( '"%s" = "%s";' % ( rec["key"], rec["text"] ) )
		lines.append( '\n' )

	# mkdir conditionally
	dir, basename= os.path.split( iosFilePath )
	if not os.path.exists( dir ):
		os.makedirs( dir )

	_infoTs( "Writing result for '%s' to ios File '%s'" % ( targetLang, iosFilePath ) )
	outputF= codecs.open( iosFilePath , "w" , encoding='utf-8' )
	outputF.write( "\n".join( lines ) )
	outputF.close()


#################################################################################
def translateForLanguages ( translationKeys, comments, requestFiles, gcloudOutputPaths, localizableStringsPaths ) :
	"""
Given a list of json request files, gcloud output paths and paths of Localizable.strings
(the path should be indicative of the target language e.g "de.DE/localizable.string"
but we wont validate it), call the gcloud translator, convert the output to iOS format
and store in the given path. We store the gcloud output for debugging purpose.

Each json request file looks as follows:

{
  'q': 'The quick brown fox jumped over the lazy dog.',
  'source': 'en',
  'target': 'es',
  'format': 'text'
}

	"""
	_dbx( requestFiles )
	_dbx( gcloudOutputPaths )
	_dbx( localizableStringsPaths )
	# loop over request files
	for ix, requestFile in enumerate( requestFiles ):
		# translate
		gcloudOutputPath= gcloudOutputPaths[ix]
		callGcloudTranslate ( requestFilePath= requestFile, outputFilePath= gcloudOutputPath ) 
		# convert and store
		
	# compute the list of formatters per translation key. do it once for all languages since the keys 
	# should always be the same. We also pray that gcloud returns the translated order in the same order!
	formattersList = [] # each list element is again a list
	for key in translationKeys:
		dummyText, formatters = parseKeyFromToGloud ( key )
		formattersList.append( formatters )
		
	for ix, outputFile in enumerate( gcloudOutputPaths ):
		# we may use either global list variable to derive the lang code. 
		# But what if for some reason, the order is not consistent?
		formatters= formattersList[ix]
		targetLang= outputFile[-2:] # fixme: pray that lang code is always 2 in length
		_dbx( outputFile )
		_dbx( targetLang )
		if True :
			convertTranslationOutputToIosFormat ( targetLang= targetLang
				, formattersList= formattersList
				, translationResultPath= gcloudOutputPaths[ix]
				, iosFilePath= localizableStringsPaths[ix] # based on index position!
				, translationKeys = translationKeys 
				, comments = comments 
				)

#################################################################################
def acquireAndStoreGToken():
	"""
request another auth-token and store it in the env var
	"""

	cmdArgs = ['gcloud'
		, 'auth'
		, 'print-access-token'
		] 
	proc= subprocess.Popen( cmdArgs ,stdin=subprocess.PIPE ,stdout=subprocess.PIPE ,stderr=subprocess.PIPE)
	msgLines, errLines= proc.communicate()
	if len( errLines ) > 0 :
		_printStdErr( "*" * 80 )
		_printStdErr( ''.join( errLines  ) )
		errorText= "".join( errLines )
				
		_errorExit( "due to previous error" )

	if len( msgLines ) > 0:
		token= "".join( msgLines )
		_infoTs( "Token requested. Run the next command and retry:\nexport %s=%s" % ( g_gTokenEnvVarName, token), True )

#################################################################################
def callGcloudTranslate ( requestFilePath, outputFilePath ) :
	"""
given a json request file and the path to the output file, call curl to submit the
request to gcloud and capture its output. Following output types are possible:
* everything ok, store the output text to file
* the global auth-token is invalid, call another method to re-generate the token 
  and abort with a hint to restart (the code would be so messy to restart if it 
  were to restart automatically!)
* something else went wrong, abort!
	"""
	global g_authToken
	if g_authToken == None:
		if not g_gTokenEnvVarName in os.environ.keys():
			acquireAndStoreGToken()
			_errorExit( 'A gcloud token has been re-acquired. Please retry the current action' )
		else:
			g_authToken= os.environ[ g_gTokenEnvVarName ] 

	cmdArgs = ['curl'
		, '-s'
		, '-k'
		, '-H'
		, 'Content-Type: application/json'
		, '-H'
		, 'Authorization: Bearer %s' % g_authToken
		, 'https://translation.googleapis.com/language/translate/v2'
		, '-d'
		, '@%s' % requestFilePath
		] 
	proc= subprocess.Popen( cmdArgs ,stdin=subprocess.PIPE ,stdout=subprocess.PIPE ,stderr=subprocess.PIPE)
	msgLines, errLines= proc.communicate()
	if len( errLines ) > 0 :
		_printStdErr( "*" * 80 )
		_printStdErr( ''.join( errLines  ) )
		errorText= "".join( errLines )
		if errorText.find( '"status": "UNAUTHENTICATED"' ) >= 0:
			_errorExit( "token error. FIXME: re-generate" )
				
		_errorExit( "due to previous error" )

	if len( msgLines ) > 0:
		# curl apparently does not use stderr. so duplicate error text mining 
		outputText= "".join( msgLines )
		if outputText.find( '"status":' ) >= 0 :
			_printStdErr( "*" * 80 )
			_printStdErr( outputText )

			if outputText.find( '"status": "UNAUTHENTICATED"' ) >= 0:
				# _errorExit( "token error. FIXME: re-generate" )
				acquireAndStoreGToken()
				_errorExit( 'A gcloud token has been re-acquired. Please retry the current action' )

			_errorExit( "due to previous error" )

		# _dbx( "writing to '%s'" % outputFilePath )
		outF = open( outputFilePath, "w" )
		outF.write( "".join( msgLines ) )

#################################################################################
def callGenstrings ( relevantFiles, outputDir ) :
	"""
	"""
	cmdArgs = ['genstrings', '-q', '-o', outputDir, ] 

	for srcFile in relevantFiles: cmdArgs.append( srcFile )
	_dbx( " ".join( cmdArgs ) )

	proc= subprocess.Popen( cmdArgs ,stdin=subprocess.PIPE ,stdout=subprocess.PIPE ,stderr=subprocess.PIPE)
	msgLines, errLines= proc.communicate( )
	if len( msgLines ) > 0 or len( errLines ) > 0 :
		_printStdErr( ''.join( msgLines ) )
		_printStdErr( ''.join( errLines  ) )

		_errorExit( "Aborted due to previous errors" )

	appMasterStringFile= os.path.join( outputDir, g_defaultAppStringsFile )
	return appMasterStringFile

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

	relevantFiles = grepRelevantSourceFiles( appFolderPath )
	if relevantFiles.count == 0:
		_errorExit( "No relevant source files found!" )

	tempDir = tempfile.mkdtemp()
	info( "Strings file will be found in %s" % tempDir )

	callGenstrings( relevantFiles, tempDir )

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
def deployStringsFiles ( fromFolder, toFolder ):
	"""
	"""
	deployCnt= 0
	for tgtFile in glob.glob( toFolder + '/*.strings' ):
		srcFile= os.path.join( fromFolder, os.path.basename( tgtFile ) )
		# _dbx( srcFile )
		if not os.path.exists( srcFile ):
			_infoTs( "source file '%s' does not exist!" % srcFile )
		else:
			shutil.copyfile( srcFile, tgtFile )	
			# _dbx( "%s deployed" % tgtFile )
			deployCnt += 1
	return deployCnt

#################################################################################
def actionDeployIosFilesToAppProject ( iosFilesTempRoot, appFolderPath ):
	"""
	"""
	deployTot= 0
	folderCnt= 0
	# double underscore variables are to be ignored
	targetMasterStringsFile, __sourceCodeFiles, appLocalizeDirNames, targetLangs= extractAppRelevantPaths( appFolderPath )
	_infoTs( "Target master string file set to: %s " % targetMasterStringsFile )

	tempMasterStringsFile= os.path.join( iosFilesTempRoot, 'Localizable.strings' ) # fixme: we want to support other files eventually
	_infoTs( "Copy %s to %s: " % ( tempMasterStringsFile, targetMasterStringsFile ) )
	shutil.copyfile( tempMasterStringsFile, targetMasterStringsFile )
	deployTot += 1

	deployFromDirs= []
	for path in appLocalizeDirNames:
		baseName= os.path.basename( path )
		lProjDirName= os.path.join( iosFilesTempRoot, baseName )
		deployFromDirs.append( lProjDirName )

	for i, appDir in enumerate( appLocalizeDirNames ):
		folderCnt += 1
		srcDir= deployFromDirs[i]
		_infoTs( "Deploying string file from %s to %s" % ( srcDir, appDir ) )
		deployTot += deployStringsFiles( fromFolder= srcDir, toFolder= appDir )
	_infoTs( "Deployed %d files for %d folders" % ( deployTot, folderCnt ) )

#################################################################################
def escapeQuote ( text ):
	"""
	"""
	return text

#################################################################################
def parseKeyFromToGloud ( text ):
	"""
Parse the string a a localizable text key for formatter such as %d, %s.
Each occurrence is replaced with {i\} where i is growing from 0 to N.
At the same time, the formatters are recorded in an array which will be returned.
E.g. for the key 
	"Time is up in %d hours and %d minutes"
the return value will be
	"Time is up in {0\} hours and {1\} minutes" 

This method has 2 use cases:
1. before sending to gcloud, the formatters in the text key are replaced with
  placeholders so that they come back unchanged at the hopefully appropiate positions.
  In this use case, only the first return value - the converted string is used.
2. after obtaining the translation from gcloud, this method is called with the original
  text key for the array of formatters. The caller can then replace the gcloud placeholders
  {i\} with the original formatters.
	"""
	pat = re.compile( "(%d|%s)" )
	i = 0
	formatters= []
	copyFrom = 0
	newText= ""

	for m in pat.finditer( text ) :
		copyTill = m.start() # slicing operator will adjust by -1 automatically
		formatters.append( m.group() )
		# _dbx( copyFrom ) _dbx( copyTill )
		newText += text [ copyFrom : copyTill ] 
		#_dbx( newText )

		newText += "{%d\}" % i
		# _dbx( newText )
		copyFrom = m.start() + len( m.group() ) # re-init for next pass
		i += 1

	if len( formatters ) == 0: # no formatter found, return the original key
		newText = text
	else: # take care trailing text after the last formatter
		newText += text [ copyFrom : ] 

	# _dbx( text ); _dbx( newText )
	return newText, formatters

#################################################################################
def actionTranslateAppStringsFileViaGcloud ( appStringsFile, targetLangs, lProjDirNames ):
	"""
For a request file with these data:
{
  'q': 'Please wait until the current sound has stopped.',
  'q': 'To save battery life, you cannot start another timer',
  'source': 'en',
  'target': 'de',
  'format': 'text'
}

We should get back:
{
  "data": {
    "translations": [
      {
        "translatedText": "Bitte warten Sie, bis der aktuelle Sound gestoppt hat."
      },
      {
        "translatedText": "Um die Batterie zu schonen, können Sie nicht einen anderen Timer starten"
      }
    ]
  }
}
	"""
	workFolder= tempfile.mkdtemp()
	_infoTs( "Work folder set to: '%s'" % workFolder )
	_infoTs( "App strings file set to: '%s'" % appStringsFile )

	# 
	# generate request files
	# 
	requestFilePaths = []
	translationKeys, guiTexts, comments= parseAppStringsFile ( sourceFile = appStringsFile )
	_infoTs( "Count of translation keys: '%d'" % len( translationKeys ) )
	formattedList= []
	# for key in translationKeys:
	for key in translationKeys :
		newKey, dummy = parseKeyFromToGloud ( key )
		# _dbx( newKey )
		formattedList.append( "'q': '%s'" % escapeQuote( newKey ) )
	qListAsText = ",\n".join( formattedList )
	jsonTemplate = """
{leftScurly} 
  {qListAsText}
 ,'source': {sourceLang}
 ,'target': {targetLang}
 ,'format': 'text'
{rightScurly}
"""
	_infoTs( "Preparing translation query files for target languages: %s" % "; ".join( targetLangs ) )

	for targetLang in targetLangs:
		jsonText = jsonTemplate.format( qListAsText= qListAsText
			, targetLang= singleQuote( targetLang )
			, leftScurly= r"{"
			, rightScurly= r"}"
			, sourceLang= r"'en'"
		)
		requestFilePath =  os.path.join( workFolder, "translationResult.json." + targetLang )
		requestFilePaths.append( requestFilePath )
		# _dbx( "writing to '%s'.." % requestFilePath )
		oFile = open( requestFilePath, 'w' )
		oFile.write( jsonText )
		oFile.close()

	_dbx( targetLangs )
	_dbx( requestFilePaths )
	# compile transation result file paths
	gcloudOutputPaths= []
	for targetLang in targetLangs:
		gcloudOutputPath =  os.path.join( workFolder, "translateRequest.json." + targetLang )
		gcloudOutputPaths.append( gcloudOutputPath )

	# compile iOS  strings file paths
	stringsFileRoot= tempfile.mkdtemp()
	_infoTs( "stringsFileRoot: '%s'" % stringsFileRoot )

	_dbx( gcloudOutputPaths )
	iosFilePaths= []
	for i, targetLang in enumerate( targetLangs ):
		subdir= lProjDirNames[i]
		_dbx( subdir )
		iosFilePath =  os.path.join( stringsFileRoot, subdir, g_defaultAppStringsFile )
		iosFilePaths.append( iosFilePath )

	_dbx( iosFilePaths )
	translateForLanguages( translationKeys= translationKeys
		, comments= comments
		, requestFiles= requestFilePaths
		, gcloudOutputPaths= gcloudOutputPaths
		, localizableStringsPaths = iosFilePaths ) 

	return gcloudOutputPaths, iosFilePaths, stringsFileRoot

#################################################################################
def extractAppRelevantPaths ( projectFolder ):
	"""
	"""
	masterStringsFile=None; sourceCodeFiles = []; localizableFolders= []; targetLangs = []

	langSupportDirPattern= re.compile( "^[a-z]{2}.*\.lproj$" ) #fixme: how do we correctly match optional string such as in zh-Hans.lproj ?
	mapLang2IosPath = {}
	for curRoot, dirs, files in os.walk ( projectFolder ):
		for dir in dirs:
			match= langSupportDirPattern.match( dir )
			if match: 
				lang= dir[0:2]
				targetLangs.append( lang )
				dirPath= os.path.join( curRoot, dir )
				# _dbx( lang ); _dbx( dirPath )
				mapLang2IosPath[ lang ] = dirPath
		for file in files:
			namePrefix, nameSuffix= os.path.splitext( file )
			# _dbx( namePrefix ); _dbx( nameSuffix )
			# list relevant file extensions here 	
			if nameSuffix in ( '.swift', '.m'):
				filePath= os.path.join( curRoot, file )
				# _dbx( "filePath: %s" % filePath )
				sourceCodeFiles.append( os.path.join(curRoot, file) )
			else:
				dummy, pathTail= os.path.split( curRoot )
				if pathTail == "Base.lproj" and file == g_defaultAppStringsFile:
					masterStringsFile= os.path.join( curRoot, file )
					_dbx( masterStringsFile )

	uniqueTargetLangs = set( targetLangs )
	if "en" in targetLangs:
		uniqueTargetLangs.remove( "en" )
	_dbx( uniqueTargetLangs )
	targetLangsNoEN = list( uniqueTargetLangs )
	# remove lang "en"
	localizableFolders= []
	for lang in targetLangsNoEN:
		localizableFolders.append( mapLang2IosPath[ lang ] )
	
	return masterStringsFile, sourceCodeFiles, localizableFolders, targetLangsNoEN


#################################################################################
def composeDiff ( oldPath, newFolder ):
	"""
	"""
	_dbx( oldPath )
	_dbx( newFolder )
	cmdArgs = ['diff', '-u', '-r', oldPath, newFolder ] 

	proc= subprocess.Popen( cmdArgs ,stdin=subprocess.PIPE ,stdout=subprocess.PIPE ,stderr=subprocess.PIPE)
	stdoutContent, errLines= proc.communicate( )
	# _dbx( type( msgLines ) );  _dbx( len( msgLines ) )
	if len( errLines ) > 0 :
		_printStdErr( ''.join( errLines  ) )

		_errorExit( "Aborted due to previous errors" )

	if type( stdoutContent ) is str: # subprocess.communicate may return str which contains newlines inside
		msgLines= stdoutContent.split( '\n' )
		# _dbx( type( msgLines ) );  _dbx( len( msgLines ) )
		return msgLines
	else:
		return stdoutContent

#################################################################################
def reportDiff ( oldFolders, newFolders, outputDir ):
	"""
assuming the strings files in each directory are utf-8, call diff -u to compare and 
pipe the output to one single output file. "diff -r -u" on each pair of old and new dir 
with matching target language may be sufficient
	"""
	# _dbx( "; ".join( oldFolders ) )
	# _dbx( "; ".join( newFolders ) )

	oldFoldersHash= {}
	newFoldersHash= {}
	# build dictionaries so we can pair by language code
	for path in newFolders:
		pathTail= os.path.basename( path )
		lang= pathTail[0:2]
		newFoldersHash[lang]= path 
	# _dbx( len( newFoldersHash ) )
	for path in oldFolders:
		pathTail= os.path.basename( path )
		lang= pathTail[0:2]
		oldFoldersHash[lang]= path 
	# _dbx( len( oldFoldersHash ) )

	outputPath= os.path.join( outputDir, 'diffOutput.txt' )
	# _dbx( outputPath )

	diffLinesAll= []

	for lang, oldPath in oldFoldersHash.iteritems():
		_dbx( oldPath )
		files= glob.glob( oldPath + "/*" )
		#_dbx( ";".join( files ) )
		if not lang in newFoldersHash.keys():
			_infoTs( "The translation result does not seem to have a folder for language '%s'!" % lang )
		else:
			newPath= newFoldersHash[lang]
			_dbx( newPath )
			diffLines= composeDiff( oldPath, newPath )
			# _dbx( type( diffLines ) ); _dbx( len( diffLines ) )
			diffLinesAll= diffLinesAll + diffLines 

	if len( diffLinesAll ) == 0:
		_errorExit( "Apparently no folders exist to perform diff on" )

	# _dbx( "Lines in diff report: %d" % len( diffLinesAll ) )
	_infoTs( "Peeking first few lines of diff report:\n%s" % "\n".join( diffLinesAll[0:10] ) )

	if os.path.exists( outputPath ):
		_errorExit( "File '%s' already exists" % outputPath )
	outputFh= open( outputPath, "w" )
	outputFh.write( "\n".join( diffLinesAll ) )
	outputFh.close()
	
	return outputPath	

#################################################################################
def actionLocalizeAppViaGcloud ( projectFolder ):
	"""
	"""
	_infoTs( "Got down this path" )
	# pre-processing
	targetMasterStringsFile, sourceCodeFiles, lProjDirNames, targetLangs= extractAppRelevantPaths( projectFolder )
	_dbx( targetLangs )
	_dbx( lProjDirNames )
	tempLProjDirNames= []
	for dirName in lProjDirNames:
		tempLProjDirNames.append( os.path.basename( dirName ) )
		
	tempDir= tempfile.mkdtemp()
	tempDirBaseName= os.path.basename( tempDir )
	workRoot = os.path.join( g_homeDir, 'TextLocalization_TEMP_ROOT' )
	if not os.path.exists( workRoot ): os.makedirs( workRoot )
	saveDir= os.path.join( workRoot, tempDirBaseName )
	# _dbx( "; ".join( sourceCodeFiles ) )
	_dbx( tempDir )
	_dbx( saveDir )
	os.rename( tempDir, saveDir )
	tempMasterStringsFile= callGenstrings( relevantFiles= sourceCodeFiles, outputDir= saveDir )
	_dbx( tempMasterStringsFile )

	gcloudOutputPaths, iosFilePaths, iosFilesRoot = actionTranslateAppStringsFileViaGcloud ( appStringsFile= tempMasterStringsFile, targetLangs= targetLangs, lProjDirNames= tempLProjDirNames )

	# post-processing
	# fakedResultFolders=  [ './iosFiles/it.IT' , './iosFiles/zh.ZH' ] # files are already UTF-8 

	newFolders= []
	for path in iosFilePaths:
		newFolders.append( os.path.split( path )[0] )

	for path in lProjDirNames:
		do16To8ConversionForFolder( path )

	shutil.move( tempMasterStringsFile, iosFilesRoot )
	diffReportFile= reportDiff( oldFolders= lProjDirNames, newFolders= newFolders, outputDir= saveDir )
	_infoTs( "Review diffReportFile '%s' before deploying: " %  diffReportFile)
	_infoTs( "Deploy from '%s' after review. Make sure it is a persistent location!" %  iosFilesRoot )

#################################################################################
def getFileType( filePath ):
	"""
call os command file 
	"""
	cmdArgs = ['file', filePath]
	proc= subprocess.Popen( cmdArgs ,stdin=subprocess.PIPE ,stdout=subprocess.PIPE ,stderr=subprocess.PIPE)
	msgLines, errLines= proc.communicate( ) #fixme: error handling!
	# _dbx( len( msgLines ) )
	return msgLines

#################################################################################
def convert16To8InPlace( filePath, backupDir, reverse= False ):
	"""
convert utf-encoding for text file in place
	"""
	# stdout of subprocess is probably ascii by default
	tempFile= tempfile.mktemp()
	convStdout= codecs.open( tempFile, "w", encoding='utf-8' )
	_dbx( tempFile )
	cmdArgs = ['iconv', '-f', 'UTF-16', '-t', 'UTF-8', filePath ] 

	proc= subprocess.Popen( cmdArgs ,stdin=subprocess.PIPE ,stdout=convStdout, stderr=subprocess.PIPE)
	msgLines, errLines= proc.communicate( )
	# _dbx( type( msgLines ) );_dbx( len( msgLines ) );
	# _dbx( msgLines ) 
	if len( errLines ) > 0 :
		_printStdErr( ''.join( errLines  ) )

		_errorExit( "Aborted due to previous errors" )
	# move original file to backup dir
	backupTo= os.path.join( backupDir, os.path.basename( filePath ) )
	os.rename( filePath, backupTo )
	_dbx( "Original file %s renamed to %s" % ( filePath, backupTo ) )
	# move new file to original path
	os.rename( tempFile, filePath )
	_dbx( "temp file renamed" )

#################################################################################
def do16To8ConversionForFolder( folderName, reverse= False ):
	"""
convert a utf-16 encoded text file to utf-8. If reverse flag is true
the reverse conversion will be performed (as of 2017.01.02 not implemented)
	"""
	# create a backup directory to put the original UTF-16 encoded files
	tempDir= tempfile.mkdtemp()
	tempDirBaseName= os.path.basename( tempDir )
	workRoot = os.path.join( g_homeDir, 'TextLocalization_TEMP_ROOT' )
	if not os.path.exists( workRoot ): os.makedirs( workRoot )
	backupDir= os.path.join( workRoot, tempDirBaseName )
	os.rename( tempDir, backupDir )

	for baseName in g_supportedStringFiles: 
		fullPath= os.path.join( folderName, baseName )
		# _dbx( fullPath )
		if os.path.exists( fullPath ):
			fileType= getFileType( fullPath )
			# _dbx( fileType )
			if 'UTF-16' in fileType:
				_infoTs( "File '%s' will be converted from UTF-16 to UTF-8" % fullPath )
				convert16To8InPlace( fullPath, backupDir )
			else:
				_infoTs( "File '%s' does not appear to be encoded in UTF-16" % fullPath )

#################################################################################
def actionSpecialTest():
	for path in ['/Users/bmlam/Dropbox/my-apps/TestShareSheet/TestShareSheet/zh-Hans.lproj'
		, '/Users/bmlam/Dropbox/my-apps/TestShareSheet/TestShareSheet/de.lproj' 
		] :
		do16To8ConversionForFolder( path )

#################################################################################
def main():
	argObject= parseCmdLine()
	# _errorExit( "What does a translation output with path /var/folders/kn/wnll0h5979lg2kj84_zb0xsc0000gn/T/tmpZsLO8M/zh-Hans.lproj/Localizable.strings contains Spanish?" )
	if argObject.action == 'DeployIosFilesToAppProject':
		actionDeployIosFilesToAppProject( iosFilesTempRoot= argObject.deployFrom
			, appFolderPath = argObject.xcodeProjectFolder )
	elif argObject.action == 'GenCsvFromAppStrings':
		actionGenCsvFromAppStrings( appFolderPath = argObject.xcodeProjectFolder
			, outputFile = argObject.outputCsv )
	elif argObject.action == 'LocalizeAppViaGcloud':
		actionLocalizeAppViaGcloud( projectFolder = argObject.xcodeProjectFolder )
	elif argObject.action == 'TranslateAppStringsFileViaGcloud':
		actionTranslateAppStringsFileViaGcloud( appStringsFile = argObject.appStringsFile
			, targetLangs = g_defaultTargetLangs ) #fixme: derive langs from app folder structure!
	elif argObject.action == 'SpecialTest':
		actionSpecialTest()
	else:
		_errorExit( "Action %s is not yet implemented" % ( argObject.action ) )
		
	_infoTs( "Program exited normally.", withTs= True )
if __name__ == "__main__":
	main()


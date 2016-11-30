#!/usr/bin/python

"""
This script is part of a concept for effectively managing text localization for iOS apps. The basic idea is that the localizable texts and their translations should be managed by an RDBMS so we can rationalize the translation and testing.

The encoding of the input file is currently hardcoded! Look for codecs
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

import sys
import getopt
import os
import re
import shutil # to copy files
import tempfile
import time
import codecs # for reading files in Unicode 
from sets import Set

gc_action_generate_csv= "generate_csv"

g_actions = [
	gc_action_generate_csv
	];

cmd_ln_options= {}
cmd_ln_options ['action']= gc_action_generate_csv  # mandatory option
cmd_ln_options ['source_dir']= None # mandatory 
cmd_ln_options ['output_file']= '/tmp/all_localizable_strings.txt' 
cmd_ln_options ['debug']= True

g_short_args='a:o:'
g_long_args=['action=', 'output_file=']

def debug(s):
	if cmd_ln_options ['debug'] == True:
		print("DBX:%s: %s" % (time.strftime('%X'), s) )
def info(s):  print("*INFO* %s: %s" % (time.strftime('%X'), s) )
def error(s):  sys.stderr.write("!! ERROR !! => %s\n" % s)

#################################################################################
def print_help_and_exit():
	print( 'Usage of %s <args> source_dir' % os.path.basename(sys.argv[0]) )
	print( "  Short arguments : %s" % g_short_args )
	print( '  Or long arguments:')
	for action in g_long_args:
		print( " --%s" % action )  
	print( 'Valid actions are: %s' % ", ".join(g_actions) )
	print( 'Default action is: %s' % cmd_ln_options["action"])
	sys.exit(2)

#################################################################################
def parse_opts():
	""" 
	"""

	if len(sys.argv) < 2 : print_help_and_exit()

	try:
		named_args, unnamed_args = getopt.getopt(sys.argv[1:] ,g_short_args, g_long_args)
	except getopt.GetoptError:
		# print help information and exit:
		print_help_and_exit()

	if named_args == None or unnamed_args == None:
		print_help_and_exit()
	for (k, v) in named_args:
		debug("k:%s v:%s" % (k,v) )
		if k in ("-h", "--help"):
			print_help_and_exit()
		if k in ("-a", "--action"):
			cmd_ln_options["action"]=  v
		if k in ("-t", "--output_file"):
			cmd_ln_options["output_file"]= v
	cmd_ln_options["source_dir"]= unnamed_args [0]

#################################################################################
def select_all_strings_files (p_file_tree):
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
def get_locale (p_folder_name):
	bn = os.path.basename(p_folder_name)
	tokens= bn.split('.')
	assert len(tokens) == 2, 'folder name strings file does not have exactly 2 dot separated components!'
	retval_lang= tokens[0]
	debug("retval_lang : %s" % retval_lang )
	return retval_lang

#################################################################################
def parse_record (p_record):
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
def process_strings_file (p_source_file, p_target_handle, p_language, p_territory, p_is_master):
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
		key, value, comment= parse_record( record)
		
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
def append_text_file_content (p_source_file, p_target_handle):
	fh= open( p_source_file, 'r')
	for line in fh.readlines():
		p_target_handle.write(line)

#################################################################################
def main():
	parse_opts()
	
	sel_files= ()
	source_root= cmd_ln_options["source_dir"] 
	# deal with trailing path separator
	if source_root [-1] == os.path.sep: source_root= source_root[0: -1]
	if cmd_ln_options["source_dir"] == None : 
		print_help_and_exit()

	info( "action: %s" %cmd_ln_options["action"] )
	strings_file_folders, strings_files= select_all_strings_files( p_file_tree= source_root )
		
	if cmd_ln_options["action"] == gc_action_generate_csv:
		# temp_file= tempfile.mktemp( dir= "/tmp")
		# temp_fh = open( temp_file, "w")
		output_file= cmd_ln_options["output_file"] 
		info("Concat target file is %s" % output_file)
		out_fh = codecs.open( output_file, "w", encoding='utf-16' )
		for ix in range (len ( strings_files ) ):
			source_path_complete= os.path.join(source_root, strings_files[ix] )
			debug("source_path_complete: %s" % source_path_complete)
			lang_code= get_locale( strings_file_folders[ix] )
			
			# out_fh.write("\n... Content of file \"%s\"\n\n" % (source_path_complete) )
			process_strings_file (p_source_file= source_path_complete, p_target_handle= out_fh, p_language= lang_code, p_territory=None, p_is_master=1)
			# append_text_file_content (p_source_file=source_path_complete , p_target_handle= out_fh)
		out_fh.close()
	else:
		error( "Specifiy a valid action!")
		exit(2)

if __name__ == "__main__":
	main()


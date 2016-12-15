import inspect

def _dbx ( text ):
	print( 'dbx: %s - Ln%d: %s' % ( inspect.stack()[1][3], inspect.stack()[1][2], text ) )

def _errorExit ( text ):
	print( 'ERROR raised from %s - Ln%d: %s' % ( inspect.stack()[1][3], inspect.stack()[1][2], text ) )
	sys.exit(1)


csvData = """\"How are you\";\"en\";\"US-en\";\"How are you\";\"Common greeting\"
\"I'am fine\";\"en\";\"US-en\";\"I'am fine\";\"Common answer to How Are You\"
"""

requiredFieldCnt = 5
lineNo = 0
stringKeys = []
comments = []
targetLangs = [ "de", "zh" ]

lines = csvData.split( "\n" )

for line in lines:
	lineNo += 1
	line = line.strip(' \t\n\r' )
	if len( line ) == 0:
		None # skip empty line
	else:
		fields = line.split( '";"' )
		if len( fields ) < requiredFieldCnt:
			_errorExit( "Line %d has less than %d fields" % ( lineNo, requiredFieldCnt) )
		
		key, lang, terr, dummy, comment = fields 
		key = key.lstrip('"')
		comment = commnt.rstrip('"')
		_dbx( "key is '%s'" % key )
		_dbx( "comment is '%s'" % comment )
		
		stringKeys.append( key )
		comments.append( comment )
		
for stringKey in stringKeys:
	None
	""" Do this:
import json
from pprint import pprint

jsonData = [ "'q' : 'apple'" , "'q' : 'banane'" ]

pprint( jsonData )
	"""
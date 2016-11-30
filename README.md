# TextLocalization
Some application development frameworks use one text file per target language to manage the translation of static texts appaering in the user interface.

Ideally the translation is performed after the the app code has been finalized. At that stage the localizable texts are extracted from the source code as a master set of records. This set is duplicated into each target language as a new text file. Each file contains per translatable item a placeholder that is supposed to be updated the the translator. Such a record may look like

OriginalText = "How do you do";TranslatedText="How do you do".

The second "How do you do" would be the placeholder in our example. The files are sent to the translators. After translation, the placeholder should contain "Comment vas tu" for French. The translated file is then imbedded into the application.

But suppose the application then is updated for the next release or for bugfix, the whole translation process needs to be repeated. Suppose a new item "I am fine" needs to be translated. If the process which is not smart enough, the generated text file will contain both "How do you do" (which has already been translated) and the new item. Then you would need to pay the translator over and over again for the same intellectual work.

Unfortunate, some out-of-the-box processes are indeed not smart enough. 

The tool described here provides a solution. The basic idea is that the localizable texts and their translations should be managed by an RDBMS so we can rationalize the translation. 

We consider the example of iOS applications. The text files are "Localizable.String" under the respective language/territory subfolders of an XCode projects, such as en.US, fr.FR, de.DE. Suppose we have gone thru the translation process described above, i.e. the text have been translated for one version, the script upload_localizable_strings.py will read in and merge all these files into a single .csv file which can be uploaded RDBMS.

The target table (for the .csv file) is assumed to have this layout:


	id number(20) not null
	,app_id varchar2(20) not null
	,lang varchar2(10) not null
	,territory varchar2(10) 
	,text_key  varchar2(100) not null
	,text_localized varchar2(200) 
	,text_comment  varchar2(1000) 
	, constraint app_loc_string_pk_new primary key (id)
	, constraint app_loc_string_uk1_new unique ( app_id, lang, text_key, territory)
  
  For one method to upload csv file into Oracle RDBMS, see ?

# TextLocalization
Some application development frameworks use one text file per target language to manage the translation of static texts appaering in the user interface.

Ideally the translation is performed after the the app code has been finalized. At that stage the localizable texts are extracted from the source code as a master set of records. This set is duplicated into each target language as a new text file. Each file contains per translatable item a placeholder that is supposed to be updated the the translator. Such a record may look like

OriginalText = "How do you do";TranslatedText="How do you do".

The second "How do you do" would be the placeholder in our example. The files are sent to the translators. After translation, the placeholder should contain "Comment vas tu" for French. The translated file is then imbedded into the application.

But suppose the application then is updated for the next release or for bugfix, the whole translation process needs to be repeated. Suppose a new item "I am fine" needs to be translated. If the process which is not smart enough, the generated text file will contain both "How do you do" (which has already been translated) and the new item. Then you would need to pay the translator over and over again for the same intellectual work.

Unfortunate, some out-of-the-box processes are indeed not smart enough. 

The tool described here provides a solution. The basic idea is that the localizable texts and their translations should be managed by an RDBMS so we can rationalize the translation. 

We consider the example of iOS applications. 

#!/bin/bash 

export GCLOUD_TOKEN=ya29.El-6A95WSjUcBzYSWTPkevplromvJDj0IJQ7jrkmxZgl9jxrpcgw5GwnyKgl7KDcIxuGynvdoJw5gqbaV_S85A9UNrTp-pmwECPpKEyY6bEwLe5OOxc1V_tduQYUVYuJ4A
# REQUEST_FILE=translateRequest.json.de
REQUEST_FILE=$1

curl -s -k -H 'Content-Type: application/json'  \
     -H "Authorization: Bearer $GCLOUD_TOKEN" \
     https://translation.googleapis.com/language/translate/v2 \
     -d "@$REQUEST_FILE"


#!/usr/bin/env bash

set -e
set -u

ANTLR_VERSION=4.7.2    # setup.py should require the same version of antlr4-python3-runtime

SEARCH_DIR="$( realpath --strip "$(dirname "$( dirname "${BASH_SOURCE[0]}" )" )" )"
QUERY_PARSER_DIR="${SEARCH_DIR}/appscale/search/query_parser"

if [ -e /usr/local/lib/antlr-${ANTLR_VERSION}-complete.jar ]
then
    echo "/usr/local/lib/antlr-${ANTLR_VERSION}-complete.jar already exists"
else
    cd /usr/local/lib
    echo "Downloading antlr4 jar from https://www.antlr.org/download/antlr-${ANTLR_VERSION}-complete.jar .."
    wget https://www.antlr.org/download/antlr-${ANTLR_VERSION}-complete.jar
fi

export CLASSPATH=".:/usr/local/lib/antlr-${ANTLR_VERSION}-complete.jar:${CLASSPATH:-}"

cd "${QUERY_PARSER_DIR}"
echo "Compiling Search API grammar SearchService2/appscale/search/query_parser/query.g4 .."
ANTLR4="java -jar /usr/local/lib/antlr-${ANTLR_VERSION}-complete.jar"
${ANTLR4} -Dlanguage=Python3 -o ./ -lib ./ -package appscale.search.query_parser ./query.g4

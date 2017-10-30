#!/bin/bash
# TODO: set this, test or release?
#  should this suffix be set from a parameter?

suffix="_test"
#suffix="_release"
#suffix="_packathon"
endpoint="cuimgsearch"

while getopts r: option
do
  case "${option}"
  in
  r) repo_path=${OPTARG};;
  esac
done

if [ ${repo_path+x} ]; then
  echo "repo_path: "${repo_path}
else
  echo "repo_path not set. Use -r to set repo_path please."
  exit -1
fi

cd ${repo_path}/setup/SentibankPyCaffeImageSearch
source ~/.bashrc


extr_type="sbpycaffe"
conf="conf_search_"${extr_type}${suffix}".json"

python ../../www/run_search_api.py -c ../../conf/${conf}  -e ${endpoint} &> log_searchapi${suffix}_${extr_type}_$(date +%Y-%m-%d_%H-%M-%S).txt


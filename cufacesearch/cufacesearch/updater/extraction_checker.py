from __future__ import print_function

import sys
import json
import time
import traceback
import multiprocessing
from datetime import datetime
from argparse import ArgumentParser
from cufacesearch.common.conf_reader import ConfReader
from cufacesearch.indexer.hbase_indexer_minimal import HBaseIndexerMinimal
from cufacesearch.ingester.generic_kafka_processor import GenericKafkaProcessor

DEFAULT_EXTR_CHECK_PREFIX = "EXTR_"


# Simulates the way updates were generated from the spark workflows but reading from a kafka topic
# Should be run as a single process to ensure data integrity,
# or all the "TODO" comments should be implemented.

class ExtractionChecker(ConfReader):
  """ExtractionChecker class.
  """

  def __init__(self, global_conf, prefix=DEFAULT_EXTR_CHECK_PREFIX, pid=None):
    """ExtractionChecker constructor.

    :param global_conf_in: configuration file or dictionary
    :type global_conf_in: str, dict
    :param prefix: prefix in configuration
    :type prefix: str
    :param pid: process id
    :type pid: int
    """
    self.list_extr_prefix = []
    self.pid = pid
    self.dict_sha1_infos = dict()

    super(ExtractionChecker, self).__init__(global_conf, prefix)

    self.last_push = time.time()
    self.nb_imgs_check = 0
    self.nb_imgs_unproc = 0
    self.nb_imgs_unproc_lastprint = 0

    self.featurizer_type = self.get_required_param("featurizer_type")
    self.detector_type = self.get_required_param("detector_type")
    self.input_type = self.get_required_param("input_type")

    # Max delay
    self.max_delay = int(self.get_param("max_delay", default=3600))

    self.list_extr_prefix = [self.featurizer_type, "feat", self.detector_type, self.input_type]
    self.extr_prefix = "_".join(self.list_extr_prefix)
    self.batch_check_column = None
    self.check_columns = []

    # changed to: get column family from indexer in set_check_columns
    # Need to be build from extraction type and detection input + "_processed"
    #self.extr_family_column = self.get_param("extr_family_column", default="ext")
    # self.extr_prefix_base_column_name = self.extr_family_column + ":" + self.extr_prefix
    # self.extr_check_column = self.extr_prefix_base_column_name + "_processed"
    # # Need to be build from extraction type and extraction input + "_batchid"
    # self.batch_check_column = self.extr_prefix_base_column_name + "_updateid"
    # self.check_columns = [self.extr_check_column, self.batch_check_column]

    self.set_pp()

    # Initialize indexer
    self.indexer = HBaseIndexerMinimal(self.global_conf,
                                       prefix=self.get_required_param("indexer_prefix"))
    self.set_check_columns()

    # Initialize ingester
    try:
      self.ingester = GenericKafkaProcessor(self.global_conf,
                                            prefix=self.get_required_param("check_ingester_prefix"))
    except Exception as inst:
      # print "Could not initialize checker, sleeping for {}s.".format(self.max_delay)
      # time.sleep(self.max_delay)
      # raise(inst)
      #print("Could not initialize 'updates_out_topic' ({}). Will write only to HBase.".format(inst))
      print("[{}: ERROR] Could not start ingester.".format(self.pp, inst))
      raise inst
    # This will not be set for HBase processing, but checker would keep dying here...
    self.updates_out_topic = None
    try:
      self.updates_out_topic = self.ingester.get_required_param("producer_updates_out_topic")
    except Exception as inst:
      # print "Could not initialize checker, sleeping for {}s.".format(self.max_delay)
      # time.sleep(self.max_delay)
      # raise(inst)
      #print("Could not initialize 'updates_out_topic' ({}). Will write only to HBase.".format(inst))
      print("{}. Will write only to HBase.".format(inst))

    self.ingester.pp = "ec"
    if self.pid:
      self.ingester.pp += str(self.pid)

  def set_check_columns(self):
    """Set columns to be checked in indexer.
    """
    # changed to: get column family from indexer
    extr_prefix_base_column_name = self.indexer.extrcf + ":" + self.extr_prefix
    extr_check_column = extr_prefix_base_column_name + "_processed"
    # Need to be build from extraction type and extraction input + "_batchid"
    self.batch_check_column = extr_prefix_base_column_name + "_updateid"
    self.check_columns = [extr_check_column, self.batch_check_column]
    #print(self.check_columns)


  def set_pp(self, pp=""):
    """Set pretty name.
    """
    self.pp = "ExtractionChecker"
    self.pp += "-".join(self.list_extr_prefix)
    if self.pid:
      self.pp += "." + str(self.pid)

  def store_img_infos(self, msg):
    """

    :param msg:
    :return:
    """
    strk = str(msg['sha1'])
    self.dict_sha1_infos[strk] = dict()
    for key in msg:
      # dumps json of 'img_info'
      # We actually need that only for DIG...
      if key == "img_info":
        self.dict_sha1_infos[strk][key] = json.dumps(msg[key])
      else:
        # discard 'img_buffer' (if it exists?...), and 'sha1'
        # if k != "img_buffer" and k != "sha1":
        #  self.dict_sha1_infos[strk][k] = msg[k]
        # discard 'sha1'
        if key != "sha1":
          self.dict_sha1_infos[strk][key] = msg[key]

  def cleanup_dict_infos(self, list_del_sha1s):
    """

    :param list_del_sha1s:
    :return:
    """
    for sha1 in list_del_sha1s:
      try:
        del self.dict_sha1_infos[str(sha1)]
      except:
        # could happen when cleaning up duplicates or image processed by another process
        pass

  def get_dict_push(self, list_get_sha1s, daemon=False):
    """

    :param list_get_sha1s:
    :param daemon:
    :return:
    """
    #TODO: is this needed for every get_dict_push call?
    self.set_check_columns()
    # TODO: also pass current update_id, and move the creation of update id out of this method
    #  this method should actually be used to 'claim' an image as soon as we can.
    dict_push = dict()
    # append processid to 'update_id' for safe use with multiple consumers, even after restart
    # /!\ beware, it should not contain underscores
    tmp_update_id, _ = self.indexer.get_next_update_id(today=None, extr_type=self.extr_prefix)
    update_id = tmp_update_id + '-' + self.ingester.pp + '-' + str(time.time())
    for sha1 in list_get_sha1s:
      dict_push[str(sha1)] = dict()
      try:
        tmp_dict = self.dict_sha1_infos[str(sha1)]
      except:
        # This would mean the image has been marked as part of another batch by another process,
        # and thus deleted in a previous 'get_unprocessed_rows' call
        # This is also only relevant if we run on Daemon mode...
        # TODO: for transition we won't really have any info to push except the update_id...
        if daemon:
          del dict_push[str(sha1)]
          continue
      # build column names properly i.e. appending 'info:'
      for key in tmp_dict:
        # changed to: use column_family from indexer
        # But the use of 'key' here also means we rely on the input to define column name...
        #dict_push[str(sha1)]['info:' + key] = tmp_dict[key]
        dict_push[str(sha1)][self.indexer.imginfocf + ':' + key] = tmp_dict[key]
      dict_push[str(sha1)][self.batch_check_column] = update_id
    return dict_push, update_id

  def get_unprocessed_rows(self, list_check_sha1s):
    """

    :param list_check_sha1s:
    :return:
    """
    # TODO: also pass current update_id and only delete if != from current update...

    unprocessed_rows = set(list_check_sha1s)

    if list_check_sha1s:
      # Check if the selected sha1 rows in HBase table 'sha1infos' have those check_column
      # This call will only return rows that DO have those check_column
      fam = self.indexer.get_dictcf_sha1_table()
      if self.verbose > 6:
        print("[{}.get_unprocessed_rows: log] fam: {}".format(self.pp, fam))
      sha1s_rows = self.indexer.get_columns_from_sha1_rows(list_check_sha1s, self.check_columns,
                                                           families=fam)
                                                           #families=self.tablesha1_col_families)
      if sha1s_rows:
        # TODO: only delete if really previously processed, i.e. if != from current update...
        found_sha1_rows = set([str(row[0]) for row in sha1s_rows])
        # Clean up 'dict_sha1_infos' deleting found_sha1_rows
        self.cleanup_dict_infos(found_sha1_rows)
        set_list_check_sha1s = set(list_check_sha1s)
        # TODO: but we should not re-add them, so we should discard them from unprocessed_rows
        unprocessed_rows = set_list_check_sha1s - found_sha1_rows

    return unprocessed_rows

  def run(self, daemon=False):
    """

    :param daemon:
    :return:
    """
    i = 0
    try:
      list_sha1s_to_process = []
      # TODO: create update_id here

      while True:
        list_check_sha1s = []

        try:
          # Accumulate images infos
          for msg_json in self.ingester.consumer:
            msg = json.loads(msg_json.value)
            # i += 1
            # print((i, len(list_check_sha1s), msg))

            # msg could now contain keys 'sha1' or 'list_sha1s'
            # should we check that we can't have both or other keys?...
            if 'sha1' in msg:
              list_check_sha1s.append(str(msg['sha1']))
              # Store other fields to be able to push them too
              self.store_img_infos(msg)
            elif 'list_sha1s' in msg:
              for sha1 in msg['list_sha1s']:
                list_check_sha1s.append(str(sha1))
                # We won't have any additional infos no?
                # But we should still build a dict for each sample for consistency...
                tmp_dict = dict()
                tmp_dict['sha1'] = str(sha1)
                # will basically push an empty dict to self.dict_sha1_infos, so self.get_dict_push
                # works properly later on...
                self.store_img_infos(tmp_dict)
            else:
              print('Unknown keys in msg: {}'.format(msg.keys()))

            if len(list_check_sha1s) >= self.indexer.batch_update_size:
              break
        except Exception as inst:
          # trying to use 'consumer_timeout_ms' to raise timeout and get last samples
          msg = "[{}: warning] At {}, caught {} {} in consumer loop"
          now_str = datetime.now().strftime('%Y-%m-%d:%H.%M.%S')
          print(msg.format(self.pp, now_str, type(inst), inst))
          sys.stdout.flush()

        if not list_check_sha1s:
          # TODO: should we fallback to scanning Hbase table here?
          continue

        # Check which images have not been processed (or pushed in an update) yet
        unprocessed_rows = self.get_unprocessed_rows(list_check_sha1s)
        self.nb_imgs_check += len(list_check_sha1s)
        push_delay = (time.time() - self.last_push) > self.max_delay / 60
        if push_delay and self.nb_imgs_unproc_lastprint != self.nb_imgs_unproc:
          msg = "[{}: log] Found {}/{} unprocessed images"
          print(msg.format(self.pp, self.nb_imgs_unproc, self.nb_imgs_check))
          self.nb_imgs_unproc_lastprint = self.nb_imgs_unproc

        # TODO: we should mark those images as being 'owned' by the update we are constructing
        # (only important if we are running multiple threads i.e. daemon is True)
        # otherwise another update running at the same time could also claim it (in another ad)
        # could be handle when adding data to the searcher but duplicates in extraction process...

        # Push sha1s to be processed
        for sha1 in unprocessed_rows:
          list_sha1s_to_process.append(sha1)

        # Remove potential duplicates
        list_sha1s_to_process = list(set(list_sha1s_to_process))

        if list_sha1s_to_process:
          # Push them to HBase by batch of 'batch_update_size'
          push_delay = (time.time() - self.last_push) > self.max_delay
          full_batch = len(list_sha1s_to_process) >= self.indexer.batch_update_size
          if full_batch or (push_delay and list_sha1s_to_process):
            # Trim here to push exactly a batch of 'batch_update_size'
            list_push = list_sha1s_to_process[:min(self.indexer.batch_update_size,
                                                   len(list_sha1s_to_process))]

            # TODO: this should be done before,
            # to 'claim' the images as soon as we plan to process them for this update
            # Gather corresponding sha1 infos
            dict_push, update_id = self.get_dict_push(list_push, daemon=daemon)
            if dict_push:
              self.nb_imgs_unproc += len(dict_push.keys())
              msg = "[{}: at {}] Pushing update {} of {} images."
              now_str = datetime.now().strftime('%Y-%m-%d:%H.%M.%S')
              print(msg.format(self.pp, now_str, update_id, len(dict_push.keys())))
              sys.stdout.flush()

              # Push images
              fam = self.indexer.get_dictcf_sha1_table()
              msg = "[{}] Pushing images for update {} with fam {}"
              print(msg.format(self.pp, update_id, fam))
              sha1s_table = self.indexer.table_sha1infos_name
              self.indexer.push_dict_rows(dict_push, sha1s_table, families=fam)

              # Build HBase updates dict
              dict_updates_db = dict()
              now_str = datetime.now().strftime('%Y-%m-%d:%H.%M.%S')
              list_sha1s_col = self.indexer.get_col_listsha1s()
              dict_updates_db[update_id] = {list_sha1s_col: ','.join(dict_push.keys()),
                                            self.indexer.get_col_upcreate(): now_str}
              # Push it
              fam = self.indexer.get_dictcf_update_table()
              msg = "[{}] Pushing update {} info with fam {}"
              print(msg.format(self.pp, update_id, fam))
              self.indexer.push_dict_rows(dict_updates_db, self.indexer.table_updateinfos_name,
                                          families=fam)

              # Build HBase updates dict
              if self.updates_out_topic is not None:
                dict_updates_kafka = dict()
                dict_updates_kafka[update_id] = ','.join(dict_push.keys())
                # Push it
                self.ingester.producer.send(self.updates_out_topic, json.dumps(dict_updates_kafka))

              # Gather any remaining sha1s and clean up infos
              if len(list_sha1s_to_process) > self.indexer.batch_update_size:
                list_sha1s_to_process = list_sha1s_to_process[self.indexer.batch_update_size:]
              else:
                list_sha1s_to_process = []
              # if duplicates wrt list_push, remove them. Can this still happen?
              list_sha1s_to_process = [sh1 for sh1 in list_sha1s_to_process if sh1 not in list_push]
              self.cleanup_dict_infos(list_push)
            else:
              msg = "[{}: at {}] Nothing to push for update {}"
              print(msg.format(self.pp, datetime.now().strftime('%Y-%m-%d:%H.%M.%S'), update_id))
              sys.stdout.flush()
            self.last_push = time.time()
            # TODO: we should create a new update_id here,
            # and let it claim the potential remaining images in 'list_sha1s_to_process'
            # sanity check that len(list_sha1s_to_process) == len(self.dict_sha1_infos) ?

    except Exception as inst:
      exc_type, exc_obj, exc_tb = sys.exc_info()
      fulltb = traceback.format_tb(exc_tb)
      raise type(inst)(" {} ({})".format(inst, ''.join(fulltb)))


class DaemonExtractionChecker(multiprocessing.Process):
  daemon = True

  def __init__(self, conf, prefix=DEFAULT_EXTR_CHECK_PREFIX):
    super(DaemonExtractionChecker, self).__init__()
    self.conf = conf
    self.prefix = prefix

  def run(self):
    """

    :return:
    """
    nb_death = 0
    while True:
      try:
        print("Starting worker ExtractionChecker.{}".format(self.pid))
        extrc = ExtractionChecker(self.conf, prefix=self.prefix, pid=self.pid)
        extrc.run(daemon=True)
      except Exception as inst:
        nb_death += 1
        # exc_type, exc_obj, exc_tb = sys.exc_info()
        # fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
        print("ExtractionChecker.{} died {}{}".format(self.pid, type(inst), inst))
        time.sleep(10 * nb_death)


if __name__ == "__main__":

  # Get conf file
  parser = ArgumentParser()
  parser.add_argument("-c", "--conf", dest="conf_file", required=True)
  parser.add_argument("-p", "--prefix", dest="prefix", default=DEFAULT_EXTR_CHECK_PREFIX)
  parser.add_argument("-d", "--deamon", dest="deamon", action="store_true", default=False)
  parser.add_argument("-w", "--workers", dest="workers", type=int, default=1)
  options = parser.parse_args()

  print("Extraction checker options are: {}".format(options))
  sys.stdout.flush()

  if options.deamon:  # use daemon
    for w in range(options.workers):
      dec = DaemonExtractionChecker(options.conf_file, prefix=options.prefix)
      dec.start()
  else:
    # Initialize extraction checker
    extrc = ExtractionChecker(options.conf_file)
    nb_death = 0
    while True:
      try:
        extrc.run()
      except Exception as inst:
        print("Extraction checker failed {}".format(inst))
        time.sleep(10 * nb_death)
        nb_death += 1

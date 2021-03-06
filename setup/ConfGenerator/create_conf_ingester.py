import os
import json
from argparse import ArgumentParser

if __name__ == "__main__":
  # Get config
  parser = ArgumentParser()
  parser.add_argument("-o", "--output_dir", dest="output_dir", required=True)
  options = parser.parse_args()

  # Environment variables:
  # - conf_name (required)
  # - input_type (required)
  # - images_topic (required, output topic)
  # If 'input_type' is local:
  # - input_path (optional, default: ./data/input_images/)
  # - source_zip (optional, e.g. to be used for an online dataset)
  # If 'input_type' is kafka:
  # - input_topic (required)
  # - input_consumer_group (required)
  # - input_obj_stored_prefix (required)
  # - input_nb_threads (optional, default: 4)
  # - kafka_servers (optional, default: memex HG kakfa brokers)
  # - kafka_security (optional)
  # TODO: report this list in the docs.
  # Make sure the docker-compose propagate all these variables down, so we can generate conf files in docker...

  # Initialization
  conf = dict()
  conf_name = os.environ['conf_name']

  kafka_servers = json.loads(os.getenv('kafka_servers', '["kafka0.team-hg-memex.com:9093",\
                                                           "kafka1.team-hg-memex.com:9093",\
                                                           "kafka2.team-hg-memex.com:9093",\
                                                           "kafka3.team-hg-memex.com:9093",\
                                                           "kafka4.team-hg-memex.com:9093",\
                                                           "kafka5.team-hg-memex.com:9093",\
                                                           "kafka6.team-hg-memex.com:9093",\
                                                           "kafka7.team-hg-memex.com:9093",\
                                                           "kafka8.team-hg-memex.com:9093",\
                                                           "kafka9.team-hg-memex.com:9093"]'))

  # Local input settings
  if os.environ['input_type'] == "local":
    prefix = "LIKP_"
    conf[prefix + 'input_path'] = os.getenv('input_path', './data/input_images/')
    source_zip = os.environ.get('source_zip')
    if source_zip:
      conf[prefix + 'source_zip'] = source_zip

  # Kafka input settings
  elif os.environ['input_type'] == "kafka":
    prefix = "KID_"
    conf[prefix + 'consumer_servers'] = kafka_servers
    conf[prefix + 'consumer_topics'] = os.environ['input_topic']
    conf[prefix + 'consumer_group'] = os.environ['input_consumer_group']
    conf[prefix + 'obj_stored_prefix'] = os.environ['input_obj_stored_prefix']
    conf[prefix + 'nb_threads'] = int(os.getenv('input_nb_threads', 4))

  else:
    raise ValueError("Unknown input type: {}".format(os.environ['input_type']))


  env_kafka_security = os.getenv('kafka_security')
  if env_kafka_security:
    kafka_security = json.loads(env_kafka_security)
    conf[prefix + 'producer_security'] = kafka_security
    conf[prefix + 'consumer_security'] = kafka_security

  conf[prefix + 'producer_servers'] = kafka_servers
  conf[prefix + 'producer_images_out_topic'] = os.environ['images_topic']
  # cdr_out_topic
  cdr_out_topic = os.getenv('cdr_out_topic', None)
  if cdr_out_topic:
    conf[prefix + 'producer_cdr_out_topic'] = cdr_out_topic

  # Generic ingestion settings
  conf[prefix + 'verbose'] = os.getenv('verbose', 0)

  if not os.path.exists(options.output_dir):
    os.mkdir(options.output_dir)

  outpath = os.path.join(options.output_dir,'conf_ingestion_'+conf_name+'.json')
  json.dump(conf, open(outpath,'wt'), sort_keys=True, indent=4)
  print("Saved conf at {}: {}".format(outpath, json.dumps(conf)))


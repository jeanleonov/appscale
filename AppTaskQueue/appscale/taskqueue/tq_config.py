""" AppScale TaskQueue configuration class. It deals with the configuration
file given with an application 'queue.yaml' or 'queue.xml'. """

import json
import logging
import os
import re
import sys

from unpackaged import APPSCALE_LIB_DIR
from unpackaged import APPSCALE_PYTHON_APPSERVER

sys.path.append(APPSCALE_PYTHON_APPSERVER)
from google.appengine.api import queueinfo

sys.path.append(APPSCALE_LIB_DIR)
import appscale_info
import file_io
import xmltodict

class TaskQueueConfig():
  """ Contains configuration of the TaskQueue system. """

  # Max concurrency per worker.
  CELERY_CONCURRENCY = 10

  # The default YAML used if a queue.yaml or queue.xml is not supplied.
  DEFAULT_QUEUE_YAML = \
"""
queue:
- name: default
  rate: 5/s
"""
 
  # The default rate for a queue if not specified in the queue.yaml. 
  # In Google App Engine it is unlimited so we use a high rate here.
  DEFAULT_RATE = "10000/s"

  # Location of all celery configuration files.
  CELERY_CONFIG_DIR = '/etc/appscale/celery/configuration/'

  # Location of all celery workers scripts.
  CELERY_WORKER_DIR = '/etc/appscale/celery/workers/'

  # Location where celery workers back up state to.
  CELERY_STATE_DIR = '/opt/appscale/celery/'

  # Directory with the task templates.
  TEMPLATE_DIR = os.path.join(
    os.path.dirname(sys.modules['appscale.taskqueue'].__file__), 'templates')

  # The location of a header of a queue worker script.
  HEADER_LOC = os.path.join(TEMPLATE_DIR, 'header.py')
  
  # The location of the task template code.
  TASK_LOC = os.path.join(TEMPLATE_DIR, 'task.py')

  # The max length allowed for a queue name.
  MAX_QUEUE_NAME_LENGTH = 100

  # A pattern for queue name validation.
  QUEUE_NAME_PATTERN = r'^[a-zA-Z0-9_]{1,%s}$' % MAX_QUEUE_NAME_LENGTH

  # A compiled regex object for queue name validation.
  QUEUE_NAME_RE = re.compile(QUEUE_NAME_PATTERN)

  # XML configs use "-" while yaml uses "_". These are the tags which 
  # need to be converted to match the yaml tags.
  YAML_TO_XML_TAGS_TO_CONVERT = ['bucket-size', 'max-concurrent-request', 
                                 'retry-parameters', 'task-age-limit', 
                                 'total-storage-limit', 'user-email',
                                 'write-email', 'min-backoff-seconds',
                                 'max-backoff-seconds', 'max-doublings',
                                 'task-retry-limit', 'max-concurrent-requests']

  # Tags from queue.xml that are ignored.
  TAGS_TO_IGNORE = ['#text']

  def __init__(self, app_id):
    """ Configuration constructor. 

    Args:
      app_id: The application ID.
    """
    file_io.set_logging_format()
    self._app_id = app_id
    file_io.mkdir(self.CELERY_CONFIG_DIR)
    file_io.mkdir(self.CELERY_WORKER_DIR)

  def get_queue_file_location(self, app_id):
    """ Gets the location of the queue.yaml or queue.xml file of a given
    application.

    Args:
      app_id: The application ID.
    Returns:
      The location of the queue.yaml or queue.xml file, and 
      an empty string if it could not be found.
    """
    queue_yaml = appscale_info.get_app_path(app_id) + 'queue.yaml'
    queue_xml = appscale_info.get_app_path(app_id) + '/war/WEB-INF/queue.xml'
    if file_io.exists(queue_yaml):
      return queue_yaml
    elif file_io.exists(queue_xml):
      return queue_xml
    else:
      return ""

  def load_queues_from_file(self, app_id):
    """ Parses the queue.yaml or queue.xml file of an application and loads
    it into the class.
   
    Args:
      app_id: The application ID.
    Returns:
      A dictionary of the queue settings.
    Raises:
      ValueError: If queue_file is unable to get loaded.
    """
    queue_file = self.get_queue_file_location(app_id)
    using_default = False
    try:
      info = file_io.read(queue_file)
      logging.info("Found queue file for app {0}".format(app_id))
    except IOError:
      logging.info("No queue file found for app {0}, using default queue".
        format(app_id))
      info = self.DEFAULT_QUEUE_YAML
      using_default = True

    #TODO handle bad xml/yaml files.
    if queue_file.endswith('yaml') or using_default:
      queue_info = queueinfo.LoadSingleQueue(info).ToDict()
    elif queue_file.endswith('xml'):
      queue_info = self.parse_queue_xml(info)
    else:
      raise ValueError("Unable to load queue information with %s" % queue_file)

    if not queue_info:
      raise ValueError("Queue information with %s not set" % queue_file)

    # We add in the default queue if its not already in there.
    has_default = False
    if 'queue' not in queue_info or len(queue_info['queue']) == 0:
      queue_info = {'queue' : [{'rate':'5/s', 'name': 'default'}]}

    for queue in queue_info['queue']:
      if queue['name'] == 'default':
        has_default = True
    if not has_default:
      queue_info['queue'].append({'rate':'5/s', 'name': 'default'})

    self._queue_info_file = queue_info
    logging.info("AppID {0} -- Loaded queue {1}".format(app_id, queue_info))
    return queue_info 

  def parse_queue_xml(self, xml_string):
    """ Turns an xml string into a dictionary tree using the same format at
    the yaml conversion.

    Args:
      xml: A string contents in XML format.
    Returns:
      A dictionary tree of the xml.
    """
    xml_dict = xmltodict.parse(xml_string)
    # Now we convert it to look the same as the yaml dictionary.
    converted = {'queue':[]}
    if isinstance(xml_dict['queue-entries']['queue'], list):
      all_queues = xml_dict['queue-entries']['queue']
    else:
      all_queues = [xml_dict['queue-entries']['queue']]

    for queue in all_queues:
      single_queue = {}
      for tag, value in queue.iteritems():
        if tag in self.YAML_TO_XML_TAGS_TO_CONVERT:
          tag = tag.replace('-','_')
        if tag == "retry_parameters":
          retry_dict = {}
          for retry_tag in queue['retry-parameters']:
            value = queue['retry-parameters'][retry_tag]
            if retry_tag in self.YAML_TO_XML_TAGS_TO_CONVERT:
              retry_tag = retry_tag.replace('-','_')
            if retry_tag not in self.TAGS_TO_IGNORE:
              retry_dict[str(retry_tag)] = str(value).strip('\n ')
          single_queue['retry_parameters'] = retry_dict
        elif tag not in self.TAGS_TO_IGNORE:
          single_queue[str(tag)] = str(value).strip('\n ')

      converted['queue'].append(single_queue)

    logging.debug("XML queue info is {0}".format(converted))
    return converted

  def create_celery_worker_scripts(self):
    """ Creates the task worker python script. It uses a configuration file
    for setup.

    Returns:
      The full path of the worker script.
    """
    header_template = file_io.read(self.HEADER_LOC)
    task_template = file_io.read(self.TASK_LOC)
    header_template = header_template.replace("APP_ID", self._app_id)
    script = header_template.replace("CELERY_CONFIGURATION", self._app_id) + \
      '\n'
    for queue in queue_info['queue']:
      if 'mode' in queue and queue['mode'] == "pull":
        # Celery does not handle pull queues.
        continue

      queue_name = queue['name']
      # The queue name is used as a function name so replace invalid chars.
      queue_name = queue_name.replace('-', '_')
      try:
        self.validate_queue_name(queue_name)
      except NameError as name_error:
        # If queue name is invalid, print exception and skip.
        logging.exception(name_error)
        continue

      new_task = task_template.\
        replace("QUEUE_NAME", self.get_queue_function_name(queue_name))
      # For tasks generated by mapreduce, or destined to be run by a module,
      # the hostname may have a prefix that corresponds to a different
      # subdomain.
      # AppScale does not support that type of routing currently, so the main
      # loadbalancer IP/hostname is used here for the execution of a task.
      new_task = new_task.\
        replace("PUBLIC_IP", "\"{}\"".format(self.get_public_ip()))
      script += new_task + '\n'

    worker_file = self.get_celery_worker_script_path(self._app_id)
    file_io.write(worker_file, script)
    return worker_file
  
  @staticmethod
  def remove_config_files(app_id):
    """ Removes Celery worker and config files.
   
    Args:
      app_id: The application ID.
    """
    worker_file = TaskQueueConfig.get_celery_worker_script_path(app_id)
    config_file = TaskQueueConfig.get_celery_configuration_path(app_id)
    file_io.delete(worker_file)
    file_io.delete(config_file)

  @staticmethod
  def get_queue_function_name(queue_name):
    """ Returns the function name of a queue which is not the queue name for
    namespacing and collision reasons.

    Args:
      queue_name: The name of a queue.
    Returns:
      The string representing the function name.
    """
    # Remove '-' because that character is not valid for a function name.
    queue_name = queue_name.replace('-', '_')
    return "queue___%s" % queue_name 

  @staticmethod
  def get_celery_annotation_name(app_id, queue_name):
    """ Returns the annotation name for a celery configuration of a queue
    for a given application id.
    
    Args:
      app_id: The application ID.
      queue_name: The application queue name.
    Returns:
      A string for the annotation tag.
    """ 
    module_name = TaskQueueConfig.get_celery_worker_module_name(app_id)
    function_name = TaskQueueConfig.get_queue_function_name(queue_name)
    return "%s.%s" % (module_name, function_name)

  @staticmethod
  def get_celery_worker_script_path(app_id):
    """ Returns the full path of the worker script used for Celery.
   
    Args:
      app_id: The application ID.
    Returns:
      A string of the full file name of the worker script.
    """
    return TaskQueueConfig.CELERY_WORKER_DIR + "app___" + app_id + ".py"

  @staticmethod
  def get_celery_worker_module_name(app_id):
    """ Returns the python module name of the queue worker script.
   
    Args:
      app_id: The application ID.
    Returns:
      A string of the module name.
    """
    return "app___" + app_id 

  @staticmethod
  def get_celery_configuration_path(app_id):
    """ Returns the full path of the configuration used for Celery.
   
    Args:
      app_id: The application ID.
    Returns:
      A string of the full file name of the configuration file.
    """
    return TaskQueueConfig.CELERY_CONFIG_DIR + app_id + ".py"

  def create_celery_file(self):
    """ Creates the Celery configuration file describing queues and exchanges
    for an application. Uses the queue.yaml/queue.xml input.

    Returns:
      A string representing the full path location of the 
      configuration file.
    """
    celery_queues = []
    celery_annotations = []
    for queue in queue_info['queue']:
      if 'mode' in queue and queue['mode'] == "pull":
        # Celery does not handle pull queues.
        continue

      queue_name = queue['name']
      # Check that the sanitized queue name is valid before configuring celery.
      queue_name = queue_name.replace('-', '_')
      try:
        self.validate_queue_name(queue_name)
      except NameError as name_error:
        # If queue name is invalid, print exception and skip.
        logging.exception(name_error)
        continue

      celery_queue_name = \
        TaskQueueConfig.get_celery_queue_name(self._app_id, queue['name'])
      celery_queues.append("Queue('" + celery_queue_name + \
         "', Exchange('" + self._app_id + \
         "'), routing_key='" + celery_queue_name  + "'),")

      rate_limit = self.DEFAULT_RATE
      if 'rate' in queue:
        rate_limit = queue['rate']

      annotation_name = \
        TaskQueueConfig.get_celery_annotation_name(self._app_id,
                                                   queue['name'])
      celery_annotations.append("'" + annotation_name + \
         "': {'rate_limit': '" + rate_limit + "'},")

    celery_queues = '\n'.join(celery_queues)
    celery_annotations = '\n'.join(celery_annotations)
    config = \
"""
from kombu import Exchange
from kombu import Queue
CELERY_QUEUES = (
"""
    config += celery_queues
    config += \
"""
)
CELERY_ANNOTATIONS = {
"""
    config += celery_annotations
    config += \
"""
}
# Everytime a task is enqueued a temporary queue is created to store
# results into rabbitmq. This can be bad in a high enqueue environment
# We use the following to make sure these temp queues are not created. 
# See http://stackoverflow.com/questions/7144025/temporary-queue-made-in-celery
# for more information on this issue.
CELERY_IGNORE_RESULT = True
CELERY_STORE_ERRORS_EVEN_IF_IGNORED = False

# One month expiration date because a task can be deferred that long.
CELERY_AMQP_TASK_RESULT_EXPIRES = 2678400

# Disable prefetching for celery workers. If tasks are small in duration this
# should be set to a higher value (64-128) for increased performance.
# See: http://celery.readthedocs.org/en/latest/userguide/optimizing.html#worker-settings
CELERYD_PREFETCH_MULTIPLIER = 1
"""
    config_file = self._app_id + ".py" 
    file_io.write(self.CELERY_CONFIG_DIR + config_file, config)
    return self.CELERY_CONFIG_DIR + config_file

  def get_public_ip(self):
    """ Gets the public IP to which the task calls are routed.

    Returns:
      The primary loadbalancer IP/hostname.
    """
    return appscale_info.get_login_ip()

  def validate_queue_name(self, queue_name):
    """ Validates the queue name to make sure it can be used as a function name.
    
    Args:
      queue_name: A string representing a queue name.
    Raises:
      NameError if the name is invalid.
    """
    if not self.QUEUE_NAME_RE.match(queue_name):
      raise NameError("Queue name %s did not match the regex %s" %\
           (queue_name, self.QUEUE_NAME_PATTERN))

  @staticmethod
  def get_celery_queue_name(app_id, queue_name):
    """ Gets a usable queue name for celery to prevent collisions where
    mulitple apps have the same name for a queue.
    
    Args:
      app_id: The application ID.
      queue_name: String name of the queue.
    Returns:
      A string to reference the queue name in celery.
    """
    return app_id + "___" + queue_name

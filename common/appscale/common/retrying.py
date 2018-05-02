import functools
import logging
import random
import traceback
import time

DEFAULT_BACKOFF_BASE = 2
DEFAULT_BACKOFF_MULTIPLIER = 0.2
DEFAULT_BACKOFF_THRESHOLD = 300
DEFAULT_MAX_RETRIES = 10
DEFAULT_RETRYING_TIMEOUT = 60
DEFAULT_RETRY_ON_EXCEPTION = (Exception, )   # Retry after any exception.
DEFAULT_RANDOMIZE = False

MISSED = object()


class BothMissedException(Exception):
  pass


class WrongUsage(RuntimeError):
  pass


def not_missed(value_1, value_2):
  if value_1 is not MISSED:
    return value_1
  if value_2 is not MISSED:
    return value_2
  raise BothMissedException()


class BackoffSequence(object):
  """
  Iterable sequence of backoff delays.
  This class suppose to be in cases when you need to do retries.
  
  Usage examples (find more in common/test/unit/test_retrying.py):
  
  1.  sequence = retrying.BackoffSequence(timeout=10)
      for backoff in sequence:
        result = do_work()
        if result == 'ok':
          break
        time.sleep(backoff)
      else:
        raise Exception('Retries did not help')
  
  2.  sequence = retrying.BackoffSequence(max_retries=3)
      for backoff in sequence:
        result = do_work()
        if result == 'ok':
          break
        if not sequence.has_more():
          raise Exception('No more retries...')
        time.sleep(backoff)
  
  3.  sequence = retrying.BackoffSequence(threshold=2, multiplier=0.02)
      for backoff in sequence:
        try:
          result = do_work()
        except Exception as err:
          time.sleep(backoff)
      else:
        raise Exception('No more retries...')
  """

  def __init__(self, base=DEFAULT_BACKOFF_BASE,
               multiplier=DEFAULT_BACKOFF_MULTIPLIER,
               threshold=DEFAULT_BACKOFF_THRESHOLD,
               max_retries=DEFAULT_MAX_RETRIES,
               timeout=DEFAULT_RETRYING_TIMEOUT,
               randomize=DEFAULT_RANDOMIZE):
    self._base = base
    self._backoff = multiplier
    self._threshold = threshold
    self._max_retries = max_retries
    self._timeout = timeout
    self._randomize = randomize
    self._attempt_num = 0
    self._promised_next = False

  def __iter__(self):
    """ Verifies if sequence was already used and returns itself.

    Returns:
      an instance of BackoffSequence (self).
    Raises:
      WrongUsage if sequence was already used.
    """
    if self._attempt_num > 0:
      raise WrongUsage("Can't iterate through BackoffSequence more than once")
    self._start_time = time.time()
    return self

  def _has_next(self, after_backoff):
    """ Private method for verifying if retries or timeout is exceeded.
    
    Args:
      after_backoff: a flag showing if timeout should be verified as for
        current moment or as if it was called after latest backoff.
    Returns:
      a boolean showing if sequence has more elements.
    """
    if self._max_retries and self._attempt_num > self._max_retries:
      return False
    if self._timeout:
      timestamp = time.time()
      if after_backoff:
        timestamp += self._backoff
      if timestamp - self._start_time > self._timeout:
        return False
    return True

  def has_next(self):
    """ Verifies if sequence has more elements.
    If it returns True, it's guarantied that next call of next() won't
    raise StopIteration()
    
    Returns:
      a boolean showing if sequence has more elements.
    """
    has_more = self._has_next(after_backoff=True)
    if has_more:
      self._promised_next = True
    return has_more

  def next(self):
    """ Computes next backoff.
     
    Returns:
      a float number representing backoff delay.
    Raises:
      StopIteration if max_retries or timeout is exceeded.
    """
    if not self._has_next(after_backoff=False) and not self._promised_next:
      raise StopIteration()

    if self._attempt_num:
      self._backoff *= self._base
    self._backoff = min(self._backoff, self._threshold)
    if self._randomize:
      self._backoff *= random.random() * 0.3 + 0.85

    self._promised_next = False
    self._attempt_num += 1
    return self._backoff

  @property
  def attempt_number(self):
    return self._attempt_num

  @property
  def start_time(self):
    return self._start_time


class _Retry(object):
  def __init__(self, backoff_base, backoff_multiplier, backoff_threshold,
               max_retries, retrying_timeout, randomize_backoff,
               retry_on_exception):
    """
    Args:
      backoff_base: a number to use in backoff calculation.
      backoff_multiplier: a number indicating initial backoff.
      backoff_threshold: a number indicating maximum backoff.
      max_retries: an integer indicating maximum number of retries.
      retrying_timeout: a number indicating number of seconds after which
        retrying should be stopped.
      randomize_backoff: a flag telling decorator to randomize backoff.
      retry_on_exception: a function receiving one argument: exception object
        and returning True if retry makes sense for such exception.
        Alternatively you can pass list of exception types for which
        retry is needed.
    """
    self.backoff_base = backoff_base
    self.backoff_multiplier = backoff_multiplier
    self.backoff_threshold = backoff_threshold
    self.max_retries = max_retries
    self.retrying_timeout = retrying_timeout
    self.randomize_backoff = randomize_backoff
    self.retry_on_exception = retry_on_exception

  def __call__(self, func=None, backoff_base=MISSED, backoff_multiplier=MISSED,
               backoff_threshold=MISSED, max_retries=MISSED,
               retrying_timeout=MISSED, randomize_backoff=MISSED,
               retry_on_exception=MISSED):
    """ Wraps func with retry mechanism which runs up to max_retries attempts
    with exponential backoff (sleep = backoff_multiplier * backoff_base**X).
    Or just creates another instance of _Retry with custom parameters.

    Args:
      func: a callable to wrap.
      backoff_base: a number to use in backoff calculation.
      backoff_multiplier: a number indicating initial backoff.
      backoff_threshold: a number indicating maximum backoff.
      max_retries: an integer indicating maximum number of retries.
      retrying_timeout: a number indicating number of seconds after which
        retrying should be stopped.
      randomize_backoff: a flag telling decorator to randomize backoff.
      retry_on_exception: a function receiving one argument: exception object
        and returning True if retry makes sense for such exception.
        Alternatively you can pass list of exception types for which
        retry is needed.
    Returns:
      A wrapped callable or parametrised instance of its class
      if function was omitted in arguments.
    """
    params = (backoff_base, backoff_multiplier, backoff_threshold, max_retries,
              retrying_timeout, retry_on_exception)

    if any((argument is not MISSED) for argument in params):
      # There are custom retrying parameters
      custom_retry = type(self)(
        backoff_base=not_missed(backoff_base, self.backoff_base),
        backoff_multiplier=not_missed(backoff_multiplier,
                                      self.backoff_multiplier),
        backoff_threshold=not_missed(backoff_threshold, self.backoff_threshold),
        max_retries=not_missed(max_retries, self.max_retries),
        retrying_timeout=not_missed(retrying_timeout, self.retrying_timeout),
        randomize_backoff=not_missed(randomize_backoff, self.randomize_backoff),
        retry_on_exception=not_missed(retry_on_exception,
                                      self.retry_on_exception)
      )

      if func is None:
        # This is probably called using decorator syntax with parameters:
        # @retry_data_watch_coroutine(retrying_timeout=60)
        # def func():
        #   ...
        return custom_retry
      else:
        # This is used as a regular function to get wrapped function.
        return custom_retry.wrap(func)

    return self.wrap(func)

  def wrap(self, func):
    """ Wraps func with retry mechanism which runs up to max_retries attempts
    with exponential backoff (sleep = backoff_multiplier * backoff_base**X).

    Args:
      func: function to wrap.
    Returns:
      A wrapped function.
    """
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
      check_exception = self.retry_on_exception

      if isinstance(check_exception, (list, tuple)):
        exception_classes = check_exception

        def check_exception_in_list(error):
          return any(
            isinstance(error, exception) for exception in exception_classes
          )

        check_exception = check_exception_in_list

      backoff_sequence = BackoffSequence(
        base=self.backoff_base,
        multiplier=self.backoff_multiplier,
        threshold=self.backoff_threshold,
        max_retries=self.max_retries,
        timeout=self.retrying_timeout,
        randomize=self.randomize_backoff
      )
      for backoff in backoff_sequence:
        try:
          # Call original function
          return func(*args, **kwargs)

        except Exception as err:
          # Check if max retries or timeout is exceeded
          if not backoff_sequence.has_next():
            retrying_time = time.time() - backoff_sequence.start_time
            logging.error(
              "Giving up retrying after {} attempts during {:0.2f}s"
              .format(backoff_sequence.attempt_number, retrying_time)
            )
            raise
          if not check_exception(err):
            raise

          # Report problem to logs
          stacktrace = traceback.format_exc()
          msg = "Retry #{} in {:0.2f}s".format(
            backoff_sequence.attempt_number, backoff)
          logging.warning(stacktrace + msg)

          # Sleep
          time.sleep(backoff)

    return wrapped


retry = _Retry(
  backoff_base=DEFAULT_BACKOFF_BASE,
  backoff_multiplier=DEFAULT_BACKOFF_MULTIPLIER,
  backoff_threshold=DEFAULT_BACKOFF_THRESHOLD,
  max_retries=DEFAULT_MAX_RETRIES,
  retrying_timeout=DEFAULT_RETRYING_TIMEOUT,
  retry_on_exception=DEFAULT_RETRY_ON_EXCEPTION,
  randomize_backoff=DEFAULT_RANDOMIZE
)

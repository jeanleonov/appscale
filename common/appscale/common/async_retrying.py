import collections
import functools
import logging
import traceback
import time

from tornado import locks, gen
from tornado.ioloop import IOLoop

from appscale.common.retrying import (
  DEFAULT_BACKOFF_BASE, DEFAULT_BACKOFF_MULTIPLIER, DEFAULT_BACKOFF_THRESHOLD,
  DEFAULT_MAX_RETRIES, DEFAULT_RETRYING_TIMEOUT, DEFAULT_RANDOMIZE,
  DEFAULT_RETRY_ON_EXCEPTION, _Retry, BackoffSequence
)


class _RetryCoroutine(_Retry):

  def wrap(self, generator):
    """ Wraps python generator with tornado coroutine and retry mechanism
    which runs up to max_retries attempts with exponential backoff
    (sleep = backoff_multiplier * backoff_base**X).

    Args:
      generator: python generator to wrap.
    Returns:
      A wrapped coroutine.
    """

    coroutine = gen.coroutine(generator)

    @functools.wraps(generator)
    @gen.coroutine
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
          # Call original coroutine
          result = yield coroutine(*args, **kwargs)
          break

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
          yield gen.sleep(backoff)

      raise gen.Return(result)

    return wrapped

retry_coroutine = _RetryCoroutine(
  backoff_base=DEFAULT_BACKOFF_BASE,
  backoff_multiplier=DEFAULT_BACKOFF_MULTIPLIER,
  backoff_threshold=DEFAULT_BACKOFF_THRESHOLD,
  max_retries=DEFAULT_MAX_RETRIES,
  retrying_timeout=DEFAULT_RETRYING_TIMEOUT,
  randomize_backoff=DEFAULT_RANDOMIZE,
  retry_on_exception=DEFAULT_RETRY_ON_EXCEPTION
)


class _PersistentWatch(object):

  class _CompliantLock(object):
    """
    A container which allows to organize compliant locking
    of zk_node by making sure:
     - update function won't be interrupted by another;
     - sleep between retries can be interrupted by newer update;
     - _PersistentWatch is able to identify and remove unused locks.
    """

    def __init__(self):
      self.waiters = 0
      self.lock = locks.Lock()
      self.condition = locks.Condition()

  def __init__(self):
    # Dict of locks for zk_nodes (shared between all functions decorated
    # by an instance of _PersistentWatch)
    self._locks = collections.defaultdict(_PersistentWatch._CompliantLock)

  def __call__(self, node, func, backoff_base=DEFAULT_BACKOFF_BASE,
               backoff_multiplier=DEFAULT_BACKOFF_MULTIPLIER,
               backoff_threshold=DEFAULT_BACKOFF_THRESHOLD,
               max_retries=DEFAULT_MAX_RETRIES,
               retrying_timeout=DEFAULT_RETRYING_TIMEOUT,
               randomize_backoff=DEFAULT_RANDOMIZE,
               retry_on_exception=DEFAULT_RETRY_ON_EXCEPTION):
    """ Wraps func with retry mechanism which runs up to max_retries attempts
    with exponential backoff (sleep = backoff_multiplier * backoff_base**X).

    Args:
      node: a string representing path to zookeeper node.
      func: function or coroutine to wrap.
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
      A tornado coroutine wrapping function with retry mechanism.
    """
    @functools.wraps(func)
    @gen.coroutine
    def persistent_execute(*args, **kwargs):
      if isinstance(retry_on_exception, (list, tuple)):
        orig_retry_on_exception = retry_on_exception

        def check_exception(error):
          return any(
            isinstance(error, exception)
            for exception in orig_retry_on_exception
          )
      else:
        check_exception = retry_on_exception

      backoff_sequence = BackoffSequence(
        base=backoff_base,
        multiplier=backoff_multiplier,
        threshold=backoff_threshold,
        max_retries=max_retries,
        timeout=retrying_timeout,
        randomize=randomize_backoff
      )
      node_lock = self._locks[node]

      # Wake older update calls (*)
      node_lock.condition.notify_all()
      node_lock.waiters += 1
      with (yield node_lock.lock.acquire()):
        node_lock.waiters -= 1

        for backoff in backoff_sequence:
          # Start of retrying iteration
          try:
            result = func(*args, **kwargs)
            if isinstance(result, gen.Future):
              result = yield result
            break

          except Exception as e:
            # Check if max retries or timeout is exceeded
            if not backoff_sequence.has_next():
              retrying_time = time.time() - backoff_sequence.start_time
              logging.error(
                "Giving up retrying after {} attempts during {:0.2f}s"
                  .format(backoff_sequence.attempt_number, retrying_time)
              )
              fail = True
            elif not check_exception(e):
              fail = True
            else:
              fail = False

            if fail:
              if not node_lock.waiters:
                del self._locks[node]
              raise

            # Report problem to logs
            stacktrace = traceback.format_exc()
            msg = "Retry #{} in {:0.2f}s".format(
              backoff_sequence.attempt_number, backoff)
            logging.warning(stacktrace + msg)

            # (*) Sleep with one eye open, give up if newer update wakes you
            now = IOLoop.current().time()
            interrupted = yield node_lock.condition.wait(now + backoff)
            if interrupted or node_lock.waiters:
              logging.info("Giving up retrying because newer update came up")
              if not node_lock.waiters:
                del self._locks[node]
              return

          # End of retrying iteration

      if not node_lock.waiters:
        del self._locks[node]
      raise gen.Return(result)

    return persistent_execute


# Two different instances for having two locks namespaces
retry_data_watch_coroutine = _PersistentWatch()
retry_children_watch_coroutine = _PersistentWatch()

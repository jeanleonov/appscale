import unittest

import logging

import time
from mock import patch, call

from appscale.common import retrying


class TestRetry(unittest.TestCase):

  @patch.object(retrying.time, 'sleep')
  @patch.object(retrying.logging, 'error')
  @patch.object(retrying.logging, 'warning')
  def test_no_errors(self, warning_mock, error_mock, sleep_mock):

    @retrying.retry
    def no_errors():
      return "No Errors"

    result = no_errors()

    # Assert outcomes.
    self.assertEqual(result, "No Errors")
    self.assertEqual(sleep_mock.call_args_list, [])
    self.assertEqual(warning_mock.call_args_list, [])
    self.assertEqual(error_mock.call_args_list, [])

  @patch.object(retrying.time, 'sleep')
  @patch.object(retrying.logging, 'error')
  @patch.object(retrying.logging, 'warning')
  @patch.object(retrying.random, 'random')
  def test_backoff_and_logging(self, random_mock, warning_mock, error_mock,
                               sleep_mock):
    random_value = 0.84
    random_mock.return_value = random_value

    @retrying.retry(
      backoff_base=3, backoff_multiplier=0.1, backoff_threshold=2,
      max_retries=4, randomize_backoff=True)
    def do_work():
      raise ValueError(u"Error \u26a0!")

    try:
      do_work()
      self.fail("Exception was expected")
    except ValueError:
      pass

    # Check backoff sleep calls (0.1 * (3 ** attempt) * random_value).
    sleep_args = [args[0] for args, kwargs in sleep_mock.call_args_list]
    self.assertAlmostEqual(sleep_args[0], 0.11, 2)
    self.assertAlmostEqual(sleep_args[1], 0.36, 2)
    self.assertAlmostEqual(sleep_args[2], 1.20, 2)
    self.assertAlmostEqual(sleep_args[3], 2.20, 2)

    # Verify logged warnings.
    expected_warnings = [
      "Retry #1 in 0.11s",
      "Retry #2 in 0.36s",
      "Retry #3 in 1.20s",
      "Retry #4 in 2.20s",
    ]
    self.assertEqual(len(expected_warnings), len(warning_mock.call_args_list))
    expected_messages = iter(expected_warnings)
    for call_args_kwargs in warning_mock.call_args_list:
      error_message = expected_messages.next()
      self.assertTrue(call_args_kwargs[0][0].startswith("Traceback"))
      self.assertTrue(call_args_kwargs[0][0].endswith(error_message))
    # Verify errors
    self.assertRegexpMatches(
      error_mock.call_args_list[0][0][0],
      "Giving up retrying after 5 attempts during -?\d+\.\d+s"
    )

  @patch.object(retrying.time, 'time')
  @patch.object(retrying.time, 'sleep')
  @patch.object(retrying.logging, 'error')
  @patch.object(retrying.logging, 'warning')
  def test_retrying_timeout(self, warning_mock, err_mock, sleep_mock,
                            time_mock):
    times = [
      100,  # Start time.
      120,  # The first retry can go (elapsed 20s less than 50s timeout).
      140,  # The second retry can go (elapsed 40s less than 50s timeout).
      160,  # Fail (elapsed 60s greater than 50s timeout).
      180,
    ]
    times.reverse()
    time_mock.side_effect = lambda : times[-1]

    @retrying.retry(
      backoff_base=3, backoff_multiplier=0.1, backoff_threshold=2,
      max_retries=10, retrying_timeout=50)
    def do_work():
      times.pop()
      raise ValueError(u"Error \u26a0!")

    try:
      do_work()
      self.fail("Exception was expected")
    except ValueError:
      pass

    # Check if there were 2 retries.
    sleep_args = [args[0] for args, kwargs in sleep_mock.call_args_list]
    self.assertEqual(len(sleep_args), 2)
    self.assertEqual(len(warning_mock.call_args_list), 2)
    # Verify errors
    self.assertEqual(
      err_mock.call_args_list,
      [call("Giving up retrying after 3 attempts during 60.00s")]
    )

  @patch.object(retrying.time, 'sleep')
  def test_exception_filter(self, sleep_mock):
    def err_filter(exception):
      return isinstance(exception, ValueError)

    @retrying.retry(retry_on_exception=err_filter)
    def func(exc_class, msg, retries_to_success):
      retries_to_success['counter'] -= 1
      if retries_to_success['counter'] <= 0:
        return "Succeeded"
      raise exc_class(msg)

    # Test retry helps.
    result = func(ValueError, "Matched", {"counter": 3})
    self.assertEqual(result, "Succeeded")

    # Test retry not applicable.
    try:
      func(TypeError, "Failed", {"counter": 3})
      self.fail("Exception was expected")
    except TypeError:
      pass


class TestBackoff(unittest.TestCase):

  def test_default_config(self):
    seq = retrying.BackoffSequence()
    self.assertEqual(
      [backoff for backoff in seq],
      [0.2, 0.4, 0.8, 1.6, 3.2, 6.4, 12.8, 25.6, 51.2, 102.4, 204.8]
    )

  def test_max_retries(self):
    seq = retrying.BackoffSequence(base=2, multiplier=0.1, max_retries=5)
    self.assertEqual(
      [backoff for backoff in seq],
      [0.1, 0.2, 0.4, 0.8, 1.6, 3.2]
    )

  @patch.object(retrying.time, 'time')
  def test_timeout(self, time_mock):
    time_mock.side_effect = [100, 105, 120, 131, 139, 142, 147, 158, 162]
    seq = retrying.BackoffSequence(
      base=2, multiplier=0.1, max_retries=1000, timeout=60
    )
    self.assertEqual(
      [backoff for backoff in seq],
      [0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4]
    )

  @patch.object(retrying.random, 'random')
  def test_randomize(self, random_mock):
    random_mock.side_effect = [0.5, 1, 0.5, 0, 0.5, 1, 0.5, 0]
    randomized_seq = retrying.BackoffSequence(
      base=2, multiplier=0.1, max_retries=7, timeout=None, randomize=True
    )
    seq = retrying.BackoffSequence(
      base=2, multiplier=0.1, max_retries=7, timeout=None
    )
    self.assertEqual(
      [round(backoff, 2) for backoff in randomized_seq],
      [0.1, 0.23, 0.46, 0.78, 1.56, 3.60, 7.19, 12.23]
    )
    self.assertEqual(
      [backoff for backoff in seq],
      [0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4, 12.8]
    )

  def test_usecase_forelse_succeded(self):
    work_results = ["ok", "failed", "failed", "failed"]
    pauses = []

    #-------------------------------------
    # USECASE: for-else
    backoff_sequence = retrying.BackoffSequence()
    for backoff in backoff_sequence:
      result = work_results.pop()
      if result == 'ok':
        break
      pauses.append(backoff)
    else:
      self.fail('Retries did not help :(')
    #-------------------------------------

    self.assertEqual(pauses, [0.2, 0.4, 0.8])
    self.assertEqual(backoff_sequence.attempt_number, 4)

  def test_usecase_forelse_failed(self):
    pauses = []

    #---------------------------------
    # USECASE: for-else
    backoff_sequence = retrying.BackoffSequence(max_retries=5)
    for backoff in backoff_sequence:
      result = 'failed'   # fail consistently
      if result == 'ok':
        self.fail('Impossible')
      pauses.append(backoff)
    else:
      logging.info('Just as expected')
    #---------------------------------

    self.assertEqual(pauses, [0.2, 0.4, 0.8, 1.6, 3.2, 6.4])
    self.assertEqual(backoff_sequence.attempt_number, 6)

  def test_usecase_hasnext_succeded(self):
    work_results = ["ok", "failed", "failed", "failed"]
    pauses = []

    #-------------------------------------
    # USECASE: has_next()
    backoff_sequence = retrying.BackoffSequence()
    for backoff in backoff_sequence:
      result = work_results.pop()
      if result == 'ok':
        break
      if not backoff_sequence.has_next():
        self.fail('Run out of retries')
      pauses.append(backoff)
    #-------------------------------------

    self.assertEqual(pauses, [0.2, 0.4, 0.8])
    self.assertEqual(backoff_sequence.attempt_number, 4)

  def test_usecase_hasnext_failed(self):
    pauses = []

    #-------------------------------------
    # USECASE: has_next()
    backoff_sequence = retrying.BackoffSequence(max_retries=5)
    for backoff in backoff_sequence:
      result = 'failed'   # fail consistently
      if result == 'ok':
        self.fail('Impossible')
      if not backoff_sequence.has_next():
        # Exit here!
        break
      pauses.append(backoff)
    else:
      self.fail('We had to exit at "Exit here!" line')
    #-------------------------------------

    self.assertEqual(pauses, [0.2, 0.4, 0.8, 1.6, 3.2])
    self.assertEqual(backoff_sequence.attempt_number, 6)


if __name__ == "__main__":
    unittest.main()

import asyncio

import psutil
import pytest
from mock import patch, MagicMock

from appscale.hermes.resources import process


def future(value=None):
  future_obj = asyncio.Future()
  future_obj.set_result(value)
  return future_obj

MONIT_STATUS = b"""
The Monit daemon 5.6 uptime: 20h 22m

Process 'haproxy'
  status                            Running
  monitoring status                 Monitored
  pid                               8466
  parent pid                        1
  uptime                            20h 21m
  children                          0
  memory kilobytes                  8140
  memory kilobytes total            8140
  memory percent                    0.2%
  memory percent total              0.2%
  cpu percent                       0.0%
  cpu percent total                 0.0%
  data collected                    Wed, 19 Apr 2017 14:15:29

File 'groomer_file_check'
  status                            Accessible
  monitoring status                 Monitored
  permission                        644

Process 'appmanagerserver'
  status                            Not monitored
  monitoring status                 Not monitored
  data collected                    Wed, 19 Apr 2017 13:49:44

Process 'app___my-25app-20003'
  status                            Running
  monitoring status                 Monitored
  pid                               5045
  parent pid                        5044
  uptime                            21h 41m
  children                          1
  memory kilobytes                  65508
  memory kilobytes total            132940
  memory percent                    1.7%
  memory percent total              3.5%
  cpu percent                       0.0%
  cpu percent total                 0.0%
  port response time                0.000s to 10.10.9.111:20000 [DEFAULT via TCP]
  data collected                    Wed, 19 Apr 2017 14:18:33

System 'appscale-image0'
  status                            Running
  monitoring status                 Monitored
  load average                      [0.23] [0.40] [0.46]
  cpu                               2.8%us 2.4%sy 1.3%wa
  memory usage                      2653952 kB [70.7%]
  swap usage                        0 kB [0.0%]
  data collected                    Wed, 19 Apr 2017 14:15:29
"""

# `systemctl status solr.service | grep 'Main PID' | awk '{ print $3 }'`
SYSTEMCTL_STATUS = b'28783'


@pytest.mark.asyncio
async def test_get_known_processes():
  # Mock `monit status` output
  monit_mock = MagicMock(returncode=0)
  stdout = MONIT_STATUS
  stderr = b''
  monit_mock.communicate.return_value = future((stdout, stderr))
  # Mock `systemctl status solr.service` output
  systemctl_mock = MagicMock(returncode=0)
  stdout = SYSTEMCTL_STATUS
  stderr = b''
  systemctl_mock.communicate.return_value = future((stdout, stderr))
  # Mock ServiceManager.get_state result
  state_mock = [
    MagicMock(process=MagicMock(pid=9850), type='datastore'),
    MagicMock(process=MagicMock(pid=9851), type='datastore'),
    MagicMock(process=MagicMock(pid=9852), type='datastore'),
    MagicMock(process=MagicMock(pid=3589), type='search'),
    MagicMock(process=MagicMock(pid=4589), type='search'),
  ]

  def fake_subprocess_shell(command, **kwargs):
    if command.startswith('monit'):
      return future(monit_mock)
    if command.startswith('systemctl'):
      return future(systemctl_mock)
    assert False, 'Unexpected command "{}"'.format(command)

  subprocess_patcher = patch(
    'asyncio.create_subprocess_shell',
    side_effect=fake_subprocess_shell
  )
  service_manager_patcher = patch(
    'appscale.admin.service_manager.ServiceManager.get_state',
    return_value=state_mock
  )

  # ^^^ ALL INPUTS ARE SPECIFIED (or mocked) ^^^
  with subprocess_patcher:
    with service_manager_patcher:
      # Calling method under test
      known_processes = await process.get_known_processes()

  # ASSERTING EXPECTATIONS
  assert known_processes == {
    8466: ['appscale', 'haproxy', 'haproxy'],
    5045: ['appscale', 'application', 'app___my-25app-20003', 'app___my-25app'],
    9850: ['appscale', 'datastore'],
    9851: ['appscale', 'datastore'],
    9852: ['appscale', 'datastore'],
    3589: ['appscale', 'search'],
    4589: ['appscale', 'search'],
    28783: ['appscale', 'solr'],
  }


@pytest.mark.asyncio
async def test_init_process_info():
  # Get info about current process
  psutil_process = psutil.Process()
  proc_info = psutil_process.as_dict(process.PROCESS_ATTRS)
  psutil_process.info = proc_info

  my_pid = psutil_process.pid
  # Call function under test
  process_ = process.init_process_info(psutil_process, {my_pid: ['test-tag']})

  # Check if attributes were assigned properly
  assert process_.pid == proc_info['pid']
  assert process_.ppid == proc_info['ppid']
  assert process_.create_time == proc_info['create_time']
  assert process_.status == proc_info['status']
  assert process_.username == proc_info['username']
  assert process_.cwd == proc_info['cwd']
  assert process_.name == proc_info['name']
  assert process_.exe == proc_info['exe']
  assert process_.cmdline == proc_info['cmdline']
  assert process_.own_tags == ['test-tag']
  assert process_.all_tags == ['test-tag']
  assert process_.cpu_user == proc_info['cpu_times'].user
  assert process_.cpu_system == proc_info['cpu_times'].system
  assert process_.cpu_user_1h_diff is None
  assert process_.cpu_system_1h_diff is None
  assert process_.cpu_percent == proc_info['cpu_percent']
  assert process_.memory_resident == proc_info['memory_info'].rss
  assert process_.memory_virtual == proc_info['memory_info'].vms
  assert process_.memory_shared == proc_info['memory_info'].shared
  assert process_.disk_io_read_count == proc_info['io_counters'].read_count
  assert process_.disk_io_write_count == proc_info['io_counters'].write_count
  assert process_.disk_io_read_bytes == proc_info['io_counters'].read_bytes
  assert process_.disk_io_write_bytes == proc_info['io_counters'].write_bytes
  assert process_.disk_io_read_count_1h_diff is None
  assert process_.disk_io_write_count_1h_diff is None
  assert process_.disk_io_read_bytes_1h_diff is None
  assert process_.disk_io_write_bytes_1h_diff is None
  assert process_.threads_num == proc_info['num_threads']
  assert process_.file_descriptors_num == proc_info['num_fds']
  assert (
    process_.ctx_switches_voluntary
    == proc_info['num_ctx_switches'].voluntary
  )
  assert (
    process_.ctx_switches_involuntary
    == proc_info['num_ctx_switches'].involuntary
  )
  assert process_.ctx_switches_voluntary_1h_diff is None
  assert process_.ctx_switches_involuntary_1h_diff is None


@pytest.mark.asyncio
async def test_list_processes():
  # Mock `monit status` with empty output
  monit_mock = MagicMock(returncode=0)
  monit_mock.communicate.return_value = future((b'', b''))
  # Mock `systemctl status solr.service` with empty output
  systemctl_mock = MagicMock(returncode=0)
  systemctl_mock.communicate.return_value = future((b'', b''))
  # Mock ServiceManager.get_state result
  state_mock = [
    MagicMock(process=MagicMock(pid=9850), type='datastore'),
    MagicMock(process=MagicMock(pid=9851), type='datastore'),
  ]
  # Mock psutil.process_iter
  fake_processes = [
    MagicMock()
  ]

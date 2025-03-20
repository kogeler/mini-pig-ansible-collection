#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright © 2025 kogeler
# SPDX-License-Identifier: Apache-2.0

import sys
import logging
import traceback
import io
import time
from urllib.parse import urlparse
import requests

from prometheus_client.parser import text_string_to_metric_families
from apscheduler.schedulers.background import BlockingScheduler
from environs import Env
from pystemd.systemd1 import Unit

LOGGING_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

app_config = {
    'log_level': 'INFO',
    'check_interval': 2,
    'restart_interval': 5,
    'max_attempts': 3,
    'prometheus_url': 'http://127.0.0.1:8001/metrics',
    'prometheus_metric_name': 'wss_alive',
    'prometheus_metric_label': 'ws_alive_url',
    'prometheus_mapping': {},
}


def uri_validator(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except:
        return False


def run_error(msg):
    print('fatal run error! ' + msg, file=sys.stderr)
    sys.exit(1)

def parse_config(config):
    env = Env()
    # SHC_LOG_LEVEL
    config['log_level'] = env.str("SHC_LOG_LEVEL", config['log_level']).upper()
    if config['log_level'] not in ['INFO', 'DEBUG']:
        run_error(f'{config["log_level"]} isn\'t a valid log level. It can be INFO or DEBUG')
    # SHC_CHECK_INTERVAL
    config['check_interval'] = env.int("SHC_CHECK_INTERVAL", config['check_interval'])
    # SHC_RESTART_INTERVAL
    config['restart_interval'] = env.int("SHC_RESTART_INTERVAL", config['restart_interval'])
    # SHC_MAX_ATTEMPTS
    config['max_attempts'] = env.int("SHC_MAX_ATTEMPTS", config['max_attempts'])
    # SHC_PROMETHEUS_URL
    config['prometheus_url'] = env.str("SHC_PROMETHEUS_URL", config['prometheus_url'])
    if not uri_validator(config['prometheus_url']):
        run_error(f'{config["prometheus_url"]} isn\'t a valid URL')
    # SHC_PROMETHEUS_METRIC_NAME
    config['prometheus_metric_name'] = env.str("SHC_PROMETHEUS_METRIC_NAME", config['prometheus_metric_name'])
    # SHC_PROMETHEUS_METRIC_LABEL
    config['prometheus_metric_label'] = env.str("SHC_PROMETHEUS_METRIC_LABEL", config['prometheus_metric_label'])
    # SHC_PROMETHEUS_MAPPING
    config['prometheus_mapping'] = env.dict("SHC_PROMETHEUS_MAPPING", config['prometheus_mapping'], subcast_values=str)
    print('config:')
    for config_line in sorted(config.items()):
        print(f' {config_line[0]}: {config_line[1]}')


def handle_exceptions(func):
    '''Decorator that handles all exceptions.'''

    def wrap(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logging.error(f'{func.__name__} function raised the exception, error: "{e}"')
            tb_output = io.StringIO()
            traceback.print_tb(e.__traceback__, file=tb_output)
            logging.debug(f'{func.__name__} function raised the exception, '
                          f'traceback:\n{tb_output.getvalue()}')
            tb_output.close()
            return None
    return wrap


def parse_prometheus_metric(url, metric_name, metric_label):
    results = {}
    metrics = requests.get(url).content
    for family in text_string_to_metric_families(metrics.decode()):
        for sample in family.samples:
            if getattr(sample, 'name') == metric_name and metric_label in getattr(sample, 'labels'):
                results[getattr(sample, 'labels')[metric_label]] = getattr(sample, 'value')
    logging.debug(f'Prometheus metrics were parsed: { results }')
    return results


def get_restart_unit_job_id(unit):
    return unit + '_restart_unit_job'


def get_stop_unit_restarting_job_id(unit):
    return unit + '_stop_unit_restarting_job'


def restart_unit(unit):
    with Unit(unit) as service:
        service.Unit.Restart('replace')
        logging.info(f'The "{ unit }" unit was restarted. '
                     f'The state of the unit is "{ service.Unit.SubState.decode("utf-8") }"')


def add_unit_restart_jobs(unit):
    def stop_unit_restarting(unit):
        if scheduler.get_job(get_restart_unit_job_id(unit)):
            scheduler.get_job(get_restart_unit_job_id(unit)).pause()
        if scheduler.get_job(get_stop_unit_restarting_job_id(unit)):
            scheduler.get_job(get_stop_unit_restarting_job_id(unit)).pause()
        logging.info(f'The maximum number of restart attempts ({ app_config["max_attempts"] }) '
                     f'has been reached for the "{ unit }". '
                     f'The "{ get_restart_unit_job_id(unit) }" and "{ get_stop_unit_restarting_job_id(unit) }" '
                     'jobs were paused')
    if not scheduler.get_job(get_restart_unit_job_id(unit)):
        scheduler.add_job(id=get_restart_unit_job_id(unit),
                          name=get_restart_unit_job_id(unit),
                          func=restart_unit,
                          kwargs={'unit': unit},
                          trigger="interval",
                          seconds=app_config['restart_interval'])
        logging.info(f'"{ get_restart_unit_job_id(unit) }" job was added for the "{unit}" unit')
    if not scheduler.get_job(get_stop_unit_restarting_job_id(unit)):
        scheduler.add_job(id=get_stop_unit_restarting_job_id(unit),
                        name=get_stop_unit_restarting_job_id(unit),
                        func=stop_unit_restarting,
                        kwargs={'unit': unit},
                        trigger="interval",
                        seconds=app_config['restart_interval'] * app_config['max_attempts'] + app_config['restart_interval'] / 2)
        logging.info(f'"{ get_stop_unit_restarting_job_id(unit) }" job was added for the "{unit}" unit')
    return True

def remove_restart_jobs(unit):
    for job_id in (get_restart_unit_job_id(unit), get_stop_unit_restarting_job_id(unit)):
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
            logging.info(f'"{ job_id }" job was removed for the "{unit}" unit')
    return True

@handle_exceptions
def check_unit(unit, url):
    metrics = parse_prometheus_metric(app_config['prometheus_url'], app_config['prometheus_metric_name'], app_config['prometheus_metric_label'])
    if url not in metrics:
        logging.error(f'There is not the "{ app_config["prometheus_metric_name"] }" metric where '
                     f'the "{ app_config["prometheus_metric_label"] }" label is equal "{url}"')
        return False
    status = bool(metrics[url])
    if status:
        remove_restart_jobs(unit)
        return True
    else:
        add_unit_restart_jobs(unit)
        return True

if __name__ == '__main__':
    global scheduler

    parse_config(app_config)

    # set up console log handler
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, app_config['log_level']))
    formatter = logging.Formatter(LOGGING_FORMAT)
    console.setFormatter(formatter)
    # set up basic logging config
    logging.basicConfig(format=LOGGING_FORMAT, level=getattr(logging, app_config['log_level']), handlers=[console])

    scheduler = BlockingScheduler()
    for item in app_config['prometheus_mapping'].items():
        scheduler.add_job(id=item[0]+'_check_job',
                          name=item[0]+'_check_job',
                          func=check_unit,
                          kwargs={'unit': item[0], 'url': item[1]},
                          trigger="interval",
                          seconds=app_config['check_interval'])
    scheduler.start()

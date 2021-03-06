#!/usr/bin/python
#
# Copyright (c) 2016 Red Hat
# Luke Hinds (lhinds@redhat.com)
# This program and the accompanying materials
# are made available under the terms of the Apache License, Version 2.0
# which accompanies this distribution, and is available at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# 0.1: This script installs OpenSCAP on the remote host, and scans the
# nominated node. Post scan a report is downloaded and if '--clean' is passed
# all trace of the scan is removed from the remote system.

import argparse
from ConfigParser import SafeConfigParser
import connect
import datetime
import os
import pkg_resources
import sys

from keystoneclient.auth.identity import v2
from keystoneclient import session
from novaclient import client

__version__ = 0.1
__author__ = 'Luke Hinds (lhinds@redhat.com)'
__url__ = 'https://wiki.opnfv.org/display/functest/Functest+Security'

# Global vars
INSTALLER_IP = os.getenv('INSTALLER_IP')
oscapbin = 'sudo /bin/oscap'

# Apex Spefic var needed to query Undercloud
if os.getenv('OS_AUTH_URL') is None:
    connect.logger.error(" Enviroment variable OS_AUTH_URL is not set")
    sys.exit(0)
else:
    OS_AUTH_URL = os.getenv('OS_AUTH_URL')

# args
parser = argparse.ArgumentParser(description='OPNFV OpenSCAP Scanner')
parser.add_argument('--config', action='store', dest='cfgfile',
                    help='Config file', required=True)
args = parser.parse_args()

# Config Parser
cfgparse = SafeConfigParser()
cfgparse.read(args.cfgfile)

#  Obtain Undercloud key
remotekey = cfgparse.get('undercloud', 'remotekey')
localkey = cfgparse.get('undercloud', 'localkey')
setup = connect.SetUp(remotekey, localkey)
setup.getockey()


# Configure Nova Credentials
com = 'sudo /usr/bin/hiera admin_password'
setup = connect.SetUp(com)
keypass = setup.keystonepass()
auth = v2.Password(auth_url=OS_AUTH_URL,
                   username='admin',
                   password=str(keypass).rstrip(),
                   tenant_name='admin')
sess = session.Session(auth=auth)
nova = client.Client(2, session=sess)


def run_tests(host, nodetype):
    """ Main tool runtime function """
    user = cfgparse.get(nodetype, 'user')
    port = cfgparse.get(nodetype, 'port')
    connect.logger.info("Host: {0} Selected Profile: {1}".format(host,
                                                                 nodetype))
    connect.logger.debug("Checking internet for package installation...")
    if internet_check(host, nodetype):
        connect.logger.debug("Internet Connection OK.")
        connect.logger.debug("Creating temp file structure..")
        createfiles(host, port, user, localkey)
        connect.logger.debug("Installing OpenSCAP...")
        install_pkg(host, port, user, localkey)
        connect.logger.debug("Running scan...")
        run_scanner(host, port, user, localkey, nodetype)
        clean = cfgparse.get(nodetype, 'clean')
        connect.logger.debug("Post installation tasks....")
        post_tasks(host, port, user, localkey, nodetype)
        if clean:
            connect.logger.debug("Cleaning down environment....")
            connect.logger.debug("Removing OpenSCAP....")
            removepkg(host, port, user, localkey, nodetype)
            connect.logger.debug("Deleting tmp file and reports (remote)...")
            cleandir(host, port, user, localkey, nodetype)
    else:
        connect.logger.error("Internet timeout. Moving on to next node..")
        pass


def nova_iterate():
    """ Iterates over the Nova API to gather a list of node IP's"""
    for server in nova.servers.list():
        if server.status == 'ACTIVE' and 'compute' in server.name:
            networks = server.networks
            nodetype = 'compute'
            for host in networks['ctlplane']:
                run_tests(host, nodetype)
        # Find controller nodes, active with network on ctlplane
        elif server.status == 'ACTIVE' and 'controller' in server.name:
            networks = server.networks
            nodetype = 'controller'
            for host in networks['ctlplane']:
                run_tests(host, nodetype)


def internet_check(host, nodetype):
    """ Performs connectivity test using scripts/internet_check.py """
    import connect
    user = cfgparse.get(nodetype, 'user')
    port = cfgparse.get(nodetype, 'port')
    localpath = pkg_resources.resource_filename(
        pkg_resources.Requirement.parse("securityscanning"),
        'scripts/internet_check.py')
    remotepath = '/tmp/internet_check.py'
    com = 'python /tmp/internet_check.py'
    testconnect = connect.ConnectionManager(host, port, user, localkey,
                                            localpath, remotepath, com)
    connectionresult = testconnect.remotescript()
    if connectionresult.rstrip() == 'True':
        return True
    else:
        return False


def createfiles(host, port, user, localkey):
    """ Creates required tempfiles needed for OpenSCAP to run.
    Executes script file: /scripts/createfiles.py
    """
    import connect
    global tmpdir
    localpath = pkg_resources.resource_filename(
        pkg_resources.Requirement.parse("securityscanning"),
        'scripts/createfiles.py')
    remotepath = '/tmp/createfiles.py'
    com = 'python /tmp/createfiles.py'
    connect = connect.ConnectionManager(host, port, user, localkey,
                                        localpath, remotepath, com)
    tmpdir = connect.remotescript()


def install_pkg(host, port, user, localkey):
    """ Installs OpenSCAP binarie and main release scap content"""
    import connect
    com = 'sudo yum -y install openscap-scanner scap-security-guide'
    connect = connect.ConnectionManager(host, port, user, localkey, com)
    connect.remotecmd()


def run_scanner(host, port, user, localkey, nodetype):
    """ Peforms the actual OpenSCAP scan operation"""
    import connect
    scantype = cfgparse.get(nodetype, 'scantype')
    profile = cfgparse.get(nodetype, 'profile')
    results = cfgparse.get(nodetype, 'results')
    report = cfgparse.get(nodetype, 'report')
    secpolicy = cfgparse.get(nodetype, 'secpolicy')
    # Here is where we contruct the actual scan command
    if scantype == 'xccdf':
        cpe = cfgparse.get(nodetype, 'cpe')
        com = '{0} xccdf eval --profile {1} --results {2}/{3}' \
              ' --report {2}/{4} --cpe {5} {6}'.format(oscapbin,
                                                       profile,
                                                       tmpdir.rstrip(),
                                                       results,
                                                       report,
                                                       cpe,
                                                       secpolicy)
        connect = connect.ConnectionManager(host, port, user, localkey, com)
        connect.remotecmd()
    elif scantype == 'oval':
        com = '{0} oval eval --results {1}/{2} '
        '--report {1}/{3} {4}'.format(oscapbin, tmpdir.rstrip(),
                                      results, report, secpolicy)
        connect = connect.ConnectionManager(host, port, user, localkey, com)
        connect.remotecmd()
    else:
        com = '{0} oval-collect '.format(oscapbin)
        connect = connect.ConnectionManager(host, port, user, localkey, com)
        connect.remotecmd()


def post_tasks(host, port, user, localkey, nodetype):
    """ Create download folder for functest dashboard and download reports """
    import connect
    reports_dir = cfgparse.get(nodetype, 'reports_dir')
    dl_folder = os.path.join(reports_dir, host + "_" +
                             datetime.datetime.
                             now().strftime('%Y-%m-%d_%H-%M-%S'))
    os.makedirs(dl_folder, 0755)
    report = cfgparse.get(nodetype, 'report')
    results = cfgparse.get(nodetype, 'results')
    reportfile = '{0}/{1}'.format(tmpdir.rstrip(), report)
    connect = connect.ConnectionManager(host, port, user, localkey, dl_folder,
                                        reportfile, report, results)
    connect.download_reports()


def removepkg(host, port, user, localkey, nodetype):
    """ Removes all packages (if Clean = True is used in ini config) """
    import connect
    com = 'sudo yum -y remove openscap-scanner scap-security-guide'
    connect = connect.ConnectionManager(host, port, user, localkey, com)
    connect.remotecmd()


def cleandir(host, port, user, localkey, nodetype):
    """ Removes all scan files (if Clean = True is used in ini config) """
    import connect
    com = 'sudo rm -r {0}'.format(tmpdir.rstrip())
    connect = connect.ConnectionManager(host, port, user, localkey, com)
    connect.remotecmd()


def main():
    logging.basicConfig()
    nova_iterate()

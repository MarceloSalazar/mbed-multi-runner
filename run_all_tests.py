#!/usr/bin/env python
import argparse
import os.path
import subprocess
import datetime
from datetime import datetime
import mbed_lstools
import sys
import json
from os.path import join, abspath, dirname
from json import load, dump
import re
import logging
from multiprocessing import Pool
from time import sleep
from prettytable import PrettyTable

parser = argparse.ArgumentParser(description='Run Mbed OS tests on all connected boards with all toolchains')
parser.add_argument("-t", "--toolchain", default="", action='store', help="Specified toolchain(s). You can provide comma-separated list.")  #default is string
parser.add_argument("-j", "--jobs", default=1, action='store', help="Number of jobs. Default 1.")  #default is string
parser.add_argument("-d", "--dryrun", default=False, action='store_true', help="print commands, don't run them")  #just check if present
parser.add_argument("-f", "--folder", default="TEST_OUTPUT", action='store', help="Folder to dump test results to")  #default is string
parser.add_argument("-r", "--report", default="json", action='store', help="Output format : json, html, text, xml")  #default is string
parser.add_argument("-o", "--other_args", default="", action='store', help="Other arguments to pass as a string")  #default is string

#Warning: Using shell=True can be a security hazard.  Ignoring because I control the command parameters.
#   Used here so I could have the option to pipe output to log file (e.g. command > log.txt).

class ToolException(Exception):
    """A class representing an exception throw by the tools"""
    pass

def main():
    args = parser.parse_args()
    other_args = args.other_args
    folder = args.folder  #folder to put the results
    report_type = args.report
    current_path = os.getcwd()
    jobs_count = int(args.jobs)

    if args.toolchain:
        toolchains = args.toolchain.split(",")
    else:
        toolchains = ['GCC_ARM', 'ARM', 'IAR']

    #get list of connected boards
    mbeds = mbed_lstools.create()
    muts = mbeds.list_mbeds(filter_function=None, unique_names=True, read_details_txt=False)

    #get timestamp
    dt = datetime.now()
    timestamp =  "%s-%s-%s-%s-%s" % (dt.year, dt.month, dt.day, dt.hour, dt.minute)

    #Create directory for test results here
    # Output files go in folder structure /<test_output>/<mbed_version>/<platform>
    try:
        os.stat(folder)
    except:
        os.mkdir(folder)
    report_base = folder

    #Set up log
    log_file = current_path + "/" + folder + "/test_runner_log_" + timestamp + ".txt"
    logging.basicConfig(filename=log_file, level=logging.DEBUG)
    log = logging.getLogger("Test Runner")
    log.setLevel(logging.DEBUG)     

    logger("-----------------------------------", log)
    logger("Simple Mbed Test Runner Log", log)
    logger("-----------------------------------", log)
    logger("TIMESTAMP : " + timestamp, log)
    for mut in muts:
        logger("PLATFORM : " + mut['platform_name'], log)
    for toolchain in toolchains:
        logger("TOOLCHAIN : "  + toolchain, log)
    logger("PATH: " + current_path, log)    
    logger("RESULTS DIR: " + folder, log)
    output = subprocess.check_output("git rev-parse HEAD" , shell=True, stderr=subprocess.STDOUT) #get the branch name
    mbed_ver = re.sub(r'\W+', '', output)  #remove weird characters
    logger("Mbed OS Ver: " + mbed_ver, log)

    logger("Test Parameters: " + other_args, log)
    logger("-----------------------------------", log)

    jobs = []
    for mut in muts:
        target = mut['platform_name']

        job = {
            'target': target,
            'toolchains': toolchains,
            'timestamp': timestamp,
            'other_args': other_args,
            'report_dir': os.path.join(report_base, mbed_ver, target),
            'report_type': report_type,
            'dryrun': args.dryrun,
            'log': log
        }
        jobs.append(job)

    logger("Testing Started...", log)
    if jobs_count <= 1:
        test_seq(jobs)
    else:
        test_queue(jobs, jobs_count)


def test_worker(job):
    log = job['log']
    _rc = 255
    report_file = None

    for toolchain in job['toolchains']:
        cmd_compile = ["mbed", "test", "--compile", "-t", toolchain, "-m", job['target']] + (job['other_args'].split(' ') if job['other_args'] else [])
        logger("EXEC: %s" % ' '.join(cmd_compile), log)
        if not job['dryrun']:
            try:
                _stdout, _stderr, _rc = run_cmd(cmd_compile)
                log.debug("Command Output:" + str(_stdout))
            except Exception, e:
                log.error("Result: COMPILE COMMAND FAILED for %s %s: %s (%s)" % (toolchain, job['target'], e.args[0], e.args[1]))
                continue

        if not os.path.exists(job['report_dir']):
            log.debug("Creating report dir %s" % job['report_dir'])
            os.makedirs(job['report_dir'])

        #Set an appropriate output file name
        if job['report_type'] == "text":
            report_file = job['target'] + "_" + toolchain + "_" + job['timestamp'] + "_results.txt"
            report_arg = "--report-text"    
        elif job['report_type'] == "html" :
            report_file = job['target'] + "_" + toolchain + "_" + job['timestamp'] + "_results.html"
            report_arg = "--report-html"
        elif job['report_type'] == "xml" :
            report_file = job['target'] + "_" + toolchain + "_" + job['timestamp'] + "_results.xml"
            report_arg = "--report-junit"    
        else:
            #anytthing else, use json output
            report_file = job['target'] + "_" + toolchain + "_" + job['timestamp'] + "_results.json"
            report_arg = "--report-json"

        cmd_test = ["mbed", "test", "--run", "-t", toolchain, "-m", job['target'], report_arg, job['report_dir'] + "/" + report_file] + (job['other_args'].split(' ') if job['other_args'] else [])
        logger("EXEC: %s" % ' '.join(cmd_test), log)
        if not job['dryrun']:
            try:
                _stdout, _stderr, _rc = run_cmd(cmd_test)
                log.debug("Command Output:" + str(_stdout))
            except Exception, e:
                log.error("Result: TEST COMMAND FAILED %s %s" % (toolchain, job['target']))

    return {
        'code': _rc,
        'report_dir': job['report_dir'],
        'report_type': job['report_type'],
        'report_file': report_file
    }



# Test sequentially
def test_seq(queue):
    for item in queue:
        result = test_worker(item)

        if result['report_type'] == "json":
            log_test_summary(result['report_dir'], result['report_file'], log)

    return True


# Test in parallel
def test_queue(queue, jobs_count):
    p = Pool(processes=jobs_count)

    results = []
    for i in range(len(queue)):
        results.append(p.apply_async(test_worker, [queue[i]]))
    p.close()

    itr = 0
    while len(results):
        itr += 1
        if itr > 3600:
            p.terminate()
            p.join()
            raise Exception(255, "Test did not finish in 1 hour")

        sleep(1)
        pending = 0
        for r in results:
            if r.ready():
                try:
                    result = r.get()
                    results.remove(r)

                    if result['report_type'] == "json":
                        log_test_summary(result['report_dir'], result['report_file'], log)
                except ToolException as err:
                    if p._taskqueue.queue:
                        p._taskqueue.queue.clear()
                        sleep(1)
                    p.terminate()
                    p.join()
                    raise ToolException(err)
            else:
                pending += 1
                if pending >= jobs_count:
                    break

    results = None
    p.join()

    return True


def run_cmd(command, work_dir=None, redirect=False):
    try:
        process = subprocess.Popen(command, cwd=work_dir)
        _stdout, _stderr = process.communicate()
    except Exception as e:
        print("[OS ERROR] Command: \"%s\" (%s) %s" % (' '.join(command), e.args[0], e.args[1]))
        raise e

    return _stdout, _stderr, process.returncode


def logger(details, log):
    print(details)
    log.info(details)


def log_test_summary(output_foler_path, report_file, log):
    #open the log file 
    test_data_json_file = os.path.join(output_foler_path, report_file)
    with open (test_data_json_file, "r") as f:
        test_data = json.loads(f.read()) 
        f.close()

    #create table
    x = PrettyTable()
    x.field_names = ["target", "platform_name", "test suite", "result"," elapsed_time (sec)"]

    #TODO check if file valid, and results are available first

    #read test log for test suite results, put rows in the table
    for target_toolchain in test_data:                
        platform, toolchain = target_toolchain.split("-")
        target_test_data = test_data[target_toolchain]
        for test_suite in target_test_data:
            test_suite_data = target_test_data[test_suite]  
            x.add_row([target_toolchain, platform, test_suite, test_suite_data.get("single_test_result", "none"),test_suite_data.get("elapsed_time", "none")])

    logger(x, log)

if __name__ == '__main__':
    main()

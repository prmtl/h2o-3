#!/usr/bin/python

import sys
import os
import json
import pickle

import copy
import subprocess


"""
This script will be invoked if it is included in the post-build action of an jenkin job and the job has failed.

It will perform the following tasks:
1. attach the failure informaiton of all tests in the current build to a summary file including the following fields:
    - timestamp, jenkin_job_name, build_id, git_has, node_name, build_failure(test failed but to build failure),
    JUnit/PyUnit/RUnit/Hadoop, testName.
2. save the above summary file to s3 somewhere using the command: s3cmd put "$TEST_OUTPUT_FILE" s3://ai.h2o.tests/jenkins/
3. store the failed test info in a dictionary and save it to s3 as well;
4. for failed tests, save the txt failed test results to aid the debugging process.  Attach timestamp to file name
  in order to aid the process of cleaning out the file directory with obsolete files.
"""

# --------------------------------------------------------------------
# Main program
# --------------------------------------------------------------------

g_test_root_dir = os.path.dirname(os.path.realpath(__file__)) # directory where we are running out code from
g_script_name = ''  # store script name.
g_timestamp = ''
g_job_name = ''
g_build_id = ''
g_git_hash = ''
g_node_name = ''
g_unit_test_type = ''
g_jenkins_url = ''
g_temp_filename = os.path.join(g_test_root_dir,'tempText')  # temp file to store data curled from Jenkins





g_node_name = "Building remotely on"   # the very next string is the name of the computer node that ran the test
g_git_hash_branch = "Checking out Revision"    # next string is git hash, and the next one is (origin/branch)
g_build_timeout = "Build timed out"             # phrase when tests run too long
g_build_success = ["Finished: SUCCESS",'BUILD SUCCESSFUL']   # sentence at the end that guarantee build success

g_build_success_tests = ['generate_rest_api_docs.py','generate_java_bindings.py'] # two functions that are usually performed after build success
g_build_id_text = 'Build id is'
g_view_name = ''



# generate file names to store the final logs.
g_output_filename_failed_tests = os.path.join(g_test_root_dir,'failedMessage_failed_tests.log')
g_output_filename_passed_tests = os.path.join(g_test_root_dir,'failedMessage_passed_tests.log')
g_output_pickle_filename = os.path.join(g_test_root_dir,'failedMessage.pickle.log')

g_failed_test_info_dict = {}
g_failed_test_info_dict["7.build_failure"] = "No"   # initialize build_failure with no by default

# info used to generate timestamp
g_weekdays = 'Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday'
g_months = 'January, Feburary, March, May, April, May, June, July, August, September, October, November, December'

g_failure_occurred = False  # denote when failure actually occurred

g_failed_jobs = []                      # record job names of failed jobs
g_failed_job_java_message_types = []    # java bad message types (can be WARN:, ERRR:, FATAL:, TRACE:)
g_failed_job_java_messages = []         # record failed job java message

g_success_jobs = []                     # record job names of passed jobs
g_success_job_java_message_types = []
g_success_job_java_messages = []        # record of successful jobs bad java messages

# text you will find before you can find your java_*_*.out.txt 
g_before_java_file = ["H2O Cloud", "Node", "started with output file"]

g_java_filenames = []   # contains all java filenames for us to mine
g_java_message_type = ["WARN:", ":WARN:", "ERRR:", "FATAL:", "TRACE:"]    # bad java message types
g_all_java_message_type = ["WARN:", ":WARN:", "ERRR:", "FATAL:", "TRACE:", "DEBUG:","INFO:"]    # all java message types

g_java_general_bad_message_types = []
g_java_general_bad_messages = []        # store java messages that are not associated with any tests

g_jenkins_url = ''
g_toContinue = False

g_current_testname = ''                 # denote when we are in a test during java text scanning

g_java_start_text = 'STARTING TEST:'    # test being started in java

g_ok_java_messages = {} # dict that stores java bad messages that we can ignore
g_java_message_pickle_filename = "bad_java_messages_to_exclude.pickle"  # pickle file that store the dictionary structure that include Java error message to exclude
g_build_failed_message = ["Finished: FAILURE".lower(),'BUILD FAILED'.lower()]   # something has gone wrong.  No tests are performed.
g_summary_text_filename = ""     # filename to store the summary file (contains all logs) sent to user via email.

'''
The sole purpose of this function is to enable us to be able to call
any function that is specified as the first argument using the argument
list specified in second argument.
'''
def perform(function_name, *arguments):
    """

    Parameters
    ----------

    function_name :  python function handle
        name of functio we want to call and run
    *arguments :  Python list
        list of arguments to be passed to function_name


    :return: bool
    """
    return function_name(*arguments)


'''
This function is written to remove extra characters before the actual string we are
looking for.  The Jenkins console output is encoded using utf-8.  However, the stupid
redirect function can only encode using ASCII.  I have googled for half a day with no
results to how.  Hence, we are going to the heat and just manually get rid of the junk.
'''
def extract_true_string(string_content):
    """
    remove extra characters before the actual string we are
    looking for.  The Jenkins console output is encoded using utf-8.  However, the stupid
    redirect function can only encode using ASCII.  I have googled for half a day with no
    results to how to resolve the issue.  Hence, we are going to the heat and just manually
    get rid of the junk.

    Parameters
    ----------

    string_content :  str
        contains a line read in from jenkins console

    :return: str: contains the content of the line after the string '[0m'

    """

    startL,found,endL = string_content.partition('[0m')

    if found:
        return endL
    else:
        return string_content

"""
Function find_time is written to extract the timestamp when a job is built.
"""
def find_time(each_line,temp_func_list):
    """
    calculate the approximate date/time from the timestamp about when the job
    was built.  This information was then saved in dict g_failed_test_info_dict.
    In addition, it will delete this particular function handle off the temp_func_list
    as we do not need to perform this action again.

    Parameters
    ----------

    each_line :  str
        contains a line read in from jenkins console
    temp_func_list :  list of Python function handles
        contains a list of functions that we want to invoke to extract information from
        the Jenkins console text.

    :return: bool to determine if text mining should continue on the jenkins console text
    """
    global g_weekdays
    global g_months
    global g_failed_test_info_dict
    
    temp_strings = each_line.strip().split()

    if (len(temp_strings) > 2):
        if ((temp_strings[0] in g_weekdays) or (temp_strings[1] in g_weekdays)) and ((temp_strings[1] in g_months) or (temp_strings[2] in g_months)):
            g_failed_test_info_dict["3.timestamp"] = each_line.strip()
            temp_func_list.remove(find_time)    # found timestamp, don't need to look again for it

    return True
            
   
def find_node_name(each_line,temp_func_list):
    """
    Find the slave machine where a Jenkins job was executed on.  It will save this
    information in g_failed_test_info_dict.  In addition, it will
    delete this particular function handle off the temp_func_list as we do not need
    to perform this action again.

    Parameters
    ----------

    each_line :  str
        contains a line read in from jenkins console
    temp_func_list :  list of Python function handles
        contains a list of functions that we want to invoke to extract information from
        the Jenkins console text.

    :return: bool to determine if text mining should continue on the jenkins console text
    """
    global g_node_name
    global g_failed_test_info_dict

    if g_node_name in each_line:
        temp_strings = each_line.split()
        [start,found,endstr] = each_line.partition(g_node_name)

        if found:
            temp_strings = endstr.split()
            g_failed_test_info_dict["6.node_name"] = extract_true_string(temp_strings[1])
            temp_func_list.remove(find_node_name)

    return True


def find_git_hash_branch(each_line,temp_func_list):
    """
    Find the git hash and branch info that  a Jenkins job was taken from.  It will save this
    information in g_failed_test_info_dict.  In addition, it will delete this particular
    function handle off the temp_func_list as we do not need to perform this action again.

    Parameters
    ----------

    each_line :  str
        contains a line read in from jenkins console
    temp_func_list :  list of Python function handles
        contains a list of functions that we want to invoke to extract information from
        the Jenkins console text.

    :return: bool to determine if text mining should continue on the jenkins console text
    """
    global g_git_hash_branch
    global g_failed_test_info_dict

    if g_git_hash_branch in each_line:
        [start,found,endstr] = each_line.partition(g_git_hash_branch)
        temp_strings = endstr.strip().split()

        if len(temp_strings) > 1:
            g_failed_test_info_dict["4.git_hash"] = temp_strings[0]
            g_failed_test_info_dict["5.git_branch"] = temp_strings[1]

        temp_func_list.remove(find_git_hash_branch)

    return True


def find_build_timeout(each_line,temp_func_list):
    """
    Find if a Jenkins job has taken too long to finish and was killed.  It will save this
    information in g_failed_test_info_dict.

    Parameters
    ----------

    each_line :  str
        contains a line read in from jenkins console
    temp_func_list :  list of Python function handles
        contains a list of functions that we want to invoke to extract information from
        the Jenkins console text.

    :return: bool to determine if text mining should continue on the jenkins console text
"""
    global g_build_timeout
    global g_failed_test_info_dict
    global g_failure_occurred

    if g_build_timeout in each_line:
        g_failed_test_info_dict["8.build_timeout"] = 'Yes'
        g_failure_occurred = True
        return False    # build timeout was found, no need to continue mining the console text
    else:
        return True

def find_build_failure(each_line,temp_func_list):
    """
    Find if a Jenkins job has failed to build.  It will save this
    information in g_failed_test_info_dict.  In addition, it will delete this particular
    function handle off the temp_func_list as we do not need to perform this action again.

    Parameters
    ----------

    each_line :  str
        contains a line read in from jenkins console
    temp_func_list :  list of Python function handles
        contains a list of functions that we want to invoke to extract information from
        the Jenkins console text.

    :return: bool to determine if text mining should continue on the jenkins console text
    """
    global g_build_success
    global g_build_success_tests
    global g_failed_test_info_dict
    global g_failure_occurred
    global g_build_failed_message

    for ind in range(0,len(g_build_failed_message)):
        if g_build_failed_message[ind] in each_line.lower():
            if ((ind == 0) and (len(g_failed_jobs) > 0)):
                continue
            else:
                g_failure_occurred = True
                g_failed_test_info_dict["7.build_failure"] = 'Yes'
                temp_func_list.remove(find_build_failure)
                return False

    return True


def find_java_filename(each_line,temp_func_list):
    """
    Find if all the java_*_0.out.txt files that were mentioned in the console output.
    It will save this information in g_java_filenames as a list of strings.

    Parameters
    ----------

    each_line :  str
        contains a line read in from jenkins console
    temp_func_list :  list of Python function handles
        contains a list of functions that we want to invoke to extract information from
        the Jenkins console text.

    :return: bool to determine if text mining should continue on the jenkins console text
"""
    global g_before_java_file
    global g_java_filenames

    for each_word in g_before_java_file:
        if (each_word not in each_line):
            return True

    # line contains the name and location of java txt output filename
    temp_strings = each_line.split()
    g_java_filenames.append(temp_strings[-1])

    return True


def find_build_id(each_line,temp_func_list):
    """
    Find the build id of a jenkins job.  It will save this
    information in g_failed_test_info_dict.  In addition, it will delete this particular
    function handle off the temp_func_list as we do not need to perform this action again.

    Parameters
    ----------

    each_line :  str
        contains a line read in from jenkins console
    temp_func_list :  list of Python function handles
        contains a list of functions that we want to invoke to extract information from
        the Jenkins console text.

    :return: bool to determine if text mining should continue on the jenkins console text
    """
    global g_before_java_file
    global g_java_filenames
    global g_build_id_text
    global g_jenkins_url
    global g_output_filename
    global g_output_pickle_filename


    if g_build_id_text in each_line:
        [startStr,found,endStr] = each_line.partition(g_build_id_text)
        g_failed_test_info_dict["2.build_id"] = endStr.strip()

        temp_func_list.remove(find_build_id)
        g_jenkins_url = os.path.join('http://',g_jenkins_url,'view',g_view_name,'job',g_failed_test_info_dict["1.jobName"],g_failed_test_info_dict["2.build_id"],'artifact')


    return True

# global list of all functions that are performed extracting new build information.
g_build_func_list = [find_time,find_node_name,find_build_id,find_git_hash_branch,find_build_timeout,find_build_failure,find_java_filename]


def update_test_dict(each_line):
    """
    Extract unit tests information from the jenkins job console output.  It will save this
    information in g_failed_jobs list and setup a place holder for saving the bad java
    messages/message types in g_failed_job_java_messages, g_failed_job_java_message_types.

    Parameters
    ----------

    each_line :  str
        contains a line read in from jenkins console

    :return: bool to determine if text mining should continue on the jenkins console text
    """
    global g_ignore_test_names
    global g_failed_jobs
    global g_failed_job_java_messages
    global g_failure_occurred

    temp_strings = each_line.split()

    if (len(temp_strings) >= 5) and ("FAIL" in each_line) and ("FAILURE" not in each_line):   # found failed test

        test_name = temp_strings[-2]
        g_failed_jobs.append(test_name)
        g_failed_job_java_messages.append([]) # insert empty java messages for now
        g_failed_job_java_message_types.append([])
        g_failure_occurred = True

    return True


'''
This function is written to extract the error messages from console output and
possible from the java_*_*.out to warn users of potentially bad runs.

'''






def extract_job_build_url(url_string):
    """
    From user input, grab the jenkins job name and saved it in g_failed_test_info_dict.
    In addition, it will grab the jenkins url and the view name into g_jenkins_url, and
    g_view_name.

    Parameters
    ----------
    url_string :  str
        contains information on the jenkins job whose console output we are interested in.

    :return: none
    """
    global g_failed_test_info_dict
    global g_jenkins_url
    global g_view_name
    
    tempString = url_string.strip('/').split('/')

    if len(tempString) < 6:
        print "Illegal URL resource address.\n"
        sys.exit(1)
        
    g_failed_test_info_dict["1.jobName"] = tempString[6]
        
    g_jenkins_url = tempString[2]
    g_view_name = tempString[4]
    

def grab_java_message():
    """scan through the java output text and extract the bad java messages that may or may not happened when
    unit tests are run.  It will not record any bad java messages that are stored in g_ok_java_messages.

    :return: none
    """

    global g_temp_filename
    global g_current_testname
    global g_java_start_text
    global g_ok_java_messages
    global g_java_general_bad_messages  # store bad java messages not associated with running a unit test
    global g_java_general_bad_message_types
    global g_failure_occurred
    global g_java_message_type
    global g_all_java_message_type
    global g_toContinue

    java_messages = []      # store all bad java messages associated with running a unit test
    java_message_types = [] # store all bad java message types associated with running a unit test

    if os.path.isfile(g_temp_filename): # open temp file containing content of some java_*_0.out.txt
        java_file = open(g_temp_filename,'r')

        g_toContinue = False    # denote if a multi-line message starts

        tempMessage = ""
        messageType = ""

        for each_line in java_file:

            if (g_java_start_text in each_line):
                startStr,found,endStr = each_line.partition(g_java_start_text)

                if len(found) > 0:
                    if len(g_current_testname) > 0: # a new unit test is being started.  Save old info and move on
                        associate_test_with_java(g_current_testname,java_messages,java_message_types)
        
                    g_current_testname = endStr.strip() # record the test name
                    
                    java_messages = []
                    java_message_types = []
        
            temp_strings = each_line.strip().split()

            if (len(temp_strings) >= 6) and (temp_strings[5] in g_all_java_message_type):
                if g_toContinue == True:    # at the end of last message fragment
                    addJavaMessages(tempMessage,messageType,java_messages,java_message_types)
                    tempMessage = ""
                    messageType = ""

                # start of new message fragment
                g_toContinue = False
            else: # non standard output.  Continuation of last java message, add it to bad java message list
                if g_toContinue:

                    tempMessage += each_line    # add more java message here
                    # if len(g_current_testname) == 0:
                    #     addJavaMessages(each_line.strip(),"",java_messages,java_message_types)
                    # else:
                    #     addJavaMessages(each_line.strip(),"",java_messages,java_message_types)

            if ((len(temp_strings) > 5) and (temp_strings[5] in g_java_message_type)):  # find a bad java message
                startStr,found,endStr = each_line.partition(temp_strings[5])    # can be WARN,ERRR,FATAL,TRACE

                if found and (len(endStr.strip()) > 0):
                    tempMessage += endStr
                    messageType = temp_strings[5]
#                    if (tempMessage not in g_ok_java_messages["general"]):  # found new bad messages that cannot be ignored
                    g_toContinue = True

                        # add tempMessage to bad java message list
#                        addJavaMessages(tempMessage,temp_strings[5],java_messages,java_message_types)
        java_file.close()
                            

def addJavaMessages(tempMessage,messageType,java_messages,java_message_types):
    """
    Insert Java messages into java_messages and java_message_types if they are associated
    with a unit test or into g_java_general_bad_messages/g_java_general_bad_message_types
    otherwise.

    Parameters
    ----------
    tempMessage :  str
        contains the bad java messages
    messageType :  str
        contains the bad java message type
    java_messages : list of str
        contains the bad java message list associated with a unit test
    java_message_tuypes :  list of str
        contains the bad java message type list associated with a unit test.

    :return: none
    """
    global g_current_testname
    global g_java_general_bad_messages
    global g_java_general_bad_message_types
    global g_failure_occurred

    tempMess = tempMessage.strip()

    if (tempMess not in g_ok_java_messages["general"]):
        if (len(g_current_testname) == 0):    # java message not associated with any test name
            g_java_general_bad_messages.append(tempMess)
            g_java_general_bad_message_types.append(messageType)
            g_failure_occurred = True
        else:   # java message found during a test
            write_test = False  # do not include java message for test if False
            if (g_current_testname in g_ok_java_messages.keys()) and (tempMess in g_ok_java_messages[g_current_testname]): # test name associated with ignored Java messages
                write_test = False
            else:   # not java ignored message for current unit test
                write_test = True

            if write_test:
                java_messages.append(tempMess)
                java_message_types.append(messageType)
                g_failure_occurred = True


def associate_test_with_java(testname,java_message,java_message_type):
    """
    When a new unit test is started as indicated in the java_*_0.out.txt file,
    update the data structures that are keeping track of unit tests being run and
    bad java messages/messages types associated with each unit test.  Since a new
    unit test is being started, save all the bad java messages associated with
    the previous unit test and start a new set for the new unit test.

    Parameters
    ----------
    testname :  str
        previous unit test testname
    java_message :  list of str
        bad java messages associated with testname
    java_message_type :  list of str
        bad java message types associated with testname

    :return :  none
    """
    global g_failed_jobs                # record job names of failed jobs
    global g_failed_job_java_messages   # record failed job java message
    global g_failed_job_java_message_types

    global g_success_jobs               # record job names of passed jobs
    global g_success_job_java_messages  # record of successful jobs bad java messages
    global g_success_job_java_message_types

    if len(java_message) > 0:
        if (testname in g_failed_jobs):
            message_index = g_failed_jobs.index(testname)
            g_failed_job_java_messages[message_index] = java_message
            g_failed_job_java_message_types[message_index] = java_message_type
        else:   # job has been sucessfully executed but something still has gone wrong
            g_success_jobs.append(testname)
            g_success_job_java_messages.append(java_message)
            g_success_job_java_message_types.append(java_message_type)


def extract_java_messages():
    """
    loop through java_*_0.out.txt and extract potentially dangerous WARN/ERRR/FATAL
    messages associated with a test.  The test may even pass but something terrible
    has actually happened.

    :return: none
    """
    global g_jenkins_url
    global g_failed_test_info_dict
    global g_java_filenames

    global g_failed_jobs  # record job names of failed jobs
    global g_failed_job_java_messages # record failed job java message
    global g_failed_job_java_message_types

    global g_success_jobs # record job names of passed jobs
    global g_success_job_java_messages # record of successful jobs bad java messages
    global g_success_job_java_message_types
   
    global g_java_general_bad_messages  # store java error messages when no job is running
    global g_java_general_bad_message_types # store java error message types when no job is running.

    if (len(g_failed_jobs) > 0):  # artifacts available only during failure of some sort
        for fname in g_java_filenames:  # grab java message from each java_*_*_.out file
            temp_strings = fname.split('/')

            start_url = g_jenkins_url

            for windex in range(6,len(temp_strings)):
                start_url = os.path.join(start_url,temp_strings[windex])
            try:    # first java file path is different.  Can ignore it.
                get_console_out(start_url)  # get java text and save it in local directory for processing
                grab_java_message()         # actually process the java text output and see if we found offensive stuff
            except:
                pass

    # build up the dict structure that we are storing our data in
    if len(g_failed_jobs) > 0:
        g_failed_test_info_dict["failed_tests_info *********"] = [g_failed_jobs,g_failed_job_java_messages,g_failed_job_java_message_types]
    if len(g_success_jobs) > 0:
        g_failed_test_info_dict["passed_tests_info *********"] = [g_success_jobs,g_success_job_java_messages,g_success_job_java_message_types]

    if len(g_java_general_bad_messages) > 0:
        g_failed_test_info_dict["9.general_bad_java_messages"] = [g_java_general_bad_messages,g_java_general_bad_message_types]



def save_dict():
    """
    Save the log scraping results into logs denoted by g_output_filename_failed_tests and
    g_output_filename_passed_tests.

    :return: none
    """

    global g_test_root_dir
    global g_output_filename_failed_tests
    global g_output_filename_passed_tests
    global g_output_pickle_filename
    global g_failed_test_info_dict


    # some build can fail really early that no buid id info is stored in the console text.
    if "2.build_id" not in g_failed_test_info_dict.keys():
        g_failed_test_info_dict["2.build_id"] = "unknown"

    build_id = g_failed_test_info_dict["2.build_id"]

    g_output_filename_failed_tests = g_output_filename_failed_tests+'_build_'+build_id+'_failed_tests.log'
    g_output_filename_passed_tests = g_output_filename_passed_tests+'_build_'+build_id+'_passed_tests.log'
    g_output_pickle_filename = g_output_pickle_filename+'_build_'+build_id+'.pickle'

    allKeys = sorted(g_failed_test_info_dict.keys())

    # write out the jenkins job info into log files.
    with open(g_output_pickle_filename,'wb') as test_file:
        pickle.dump(g_failed_test_info_dict,test_file)

    # write out the failure report as text into a text file
    text_file_failed_tests = open(g_output_filename_failed_tests,'w')
    text_file_passed_tests = None
    allKeys = sorted(g_failed_test_info_dict.keys())
    write_passed_tests = False

    if ("passed_tests_info *********" in allKeys):
        text_file_passed_tests = open(g_output_filename_passed_tests,'w')
        write_passed_tests = True

    for keyName in allKeys:
        val = g_failed_test_info_dict[keyName]
        if isinstance(val,list):    # writing one of the job lists
            if (len(val) == 3):     # it is a message for a test
                if keyName == "failed_tests_info *********":
                    write_test_java_message(keyName,val,text_file_failed_tests)

                if keyName == "passed_tests_info *********":
                    write_test_java_message(keyName,val,text_file_passed_tests)
            elif (len(val) == 2):                   # it is a general bad java message
                write_java_message(keyName,val,text_file_failed_tests)
                if write_passed_tests:
                    write_java_message(keyName,val,text_file_passed_tests)
        else:
            write_general_build_message(keyName,val,text_file_failed_tests)
            if write_passed_tests:
                write_general_build_message(keyName,val,text_file_passed_tests)

    text_file_failed_tests.close()
    if write_passed_tests:
        text_file_passed_tests.close()

def write_general_build_message(key,val,text_file):
    """
    Write key/value into log file when the value is a string and not a list.

    Parameters
    ----------
    key :  str
        key value in g_failed_test_info_dict
    value :  str
        corresponding value associated with the key in key
    text_file : file handle
        file handle of log file to write the info to.


    :return: none
    """
    text_file.write(key+": ")
    text_file.write(val)
    text_file.write('\n\n')

def write_test_java_message(key,val,text_file):
    """
   Write key/value into log file when the value is a list of strings
   or even a list of list of string.  These lists are associated with
   unit tests that are executed in the jenkins job.

    Parameters
    ----------
    key :  str
        key value in g_failed_test_info_dict
    value :  list of str or list of list of str
        corresponding value associated with the key in key
    text_file : file handle
        file handle of log file to write the info to.

   :return: none
   """
    global g_failed_jobs

    text_file.write(key)
    text_file.write('\n')

    # val is a tuple of 3 tuples
    for index in range(len(val[0])):

        if ((val[0][index] in g_failed_jobs) or ((val[0][index] not in g_failed_jobs) and (len(val[1][index]) > 0))):
            text_file.write("\nTest Name: ")
            text_file.write(val[0][index])
            text_file.write('\n')

        if (len(val[1][index]) > 0) and (len(val) >= 3):
            text_file.write("Java Message Type and Message: \n")
            for eleIndex in range(len(val[1][index])):
                text_file.write(val[2][index][eleIndex]+" ")
                text_file.write(val[1][index][eleIndex])
                text_file.write('\n\n')

    text_file.write('\n')
    text_file.write('\n')

def update_summary_file():
    """
    Concatecate all log file into a summary text file to be sent to users
    at the end of a daily log scraping.

    :return: none
    """
    global g_summary_text_filename
    global g_output_filename_failed_tests
    global g_output_filename_passed_tests

    with open(g_summary_text_filename,'a') as tempfile:
        write_file_content(tempfile,g_output_filename_failed_tests)
        write_file_content(tempfile,g_output_filename_passed_tests)


def write_file_content(fhandle,file2read):
    """
    Write one log file into the summary text file.

    Parameters
    ----------
    fhandle :  Python file handle
        file handle to the summary text file
    file2read : Python file handle
        file handle to log file where we want to add its content to the summary text file.

    :return: none
    """
    if os.path.isfile(file2read):

        # write summary of failed tests logs
        with open(file2read,'r') as tfile:
            fhandle.write('============ Content of '+ file2read)
            fhandle.write('\n')
            fhandle.write(tfile.read())
            fhandle.write('\n\n')



def write_java_message(key,val,text_file):
    """
    Loop through all java messages that are not associated with a unit test and
    write them into a log file.

    Parameters
    ----------
    key :  str
        9.general_bad_java_messages
    val : list of list of str
        contains the bad java messages and the message types.


    :return: none
    """

    text_file.write(key)
    text_file.write('\n')

    if (len(val[0]) > 0) and (len(val) >= 3):
        for index in range(len(val[0])):
            text_file.write("Java Message Type: ")
            text_file.write(val[1][index])
            text_file.write('\n')

            text_file.write("Java Message: ")

            for jmess in val[2][index]:
                text_file.write(jmess)
                text_file.write('\n')

        text_file.write('\n \n')


def load_java_messages_to_ignore():
    """
    Load in pickle file that contains dict structure with bad java messages to ignore per unit test
    or for all cases.  The ignored bad java info is stored in g_ok_java_messages dict.

    :return:
    """
    global g_ok_java_messages
    global g_java_message_pickle_filename

    if os.path.isfile(g_java_message_pickle_filename):
        with open(g_java_message_pickle_filename,'rb') as tfile:
            g_ok_java_messages = pickle.load(tfile)
    else:
        g_ok_java_messages["general"] = []


def usage():
    """
    Print USAGE help.
    """
    print("")
    print("Usage:  ")
    print("python scrapeForIntermittents timestamp job_name build_id git_sha node_name unit_test_category jenkins_URL "
          "output_filename")
    print(" The unit_test_category can be 'junit', 'pyunit' or 'runit'.")

'''
This function is written to extract the console output that has already been stored
in a text file in a remote place and saved it in a local directory that we have accessed
to.  We want to be able to read in the local text file and proces it.
'''
def get_console_out(url_string):
    """
    Grab the console output from Jenkins and save the content into a temp file
     (g_temp_filename).  From the saved text file, we can grab the names of
     failed tests.

    Parameters
    ----------
    url_string :  str
        contains information on the jenkins job whose console output we are interested in.  It is in the context
        of resource_url/job/job_name/build_id/testReport/

    :return: none
    """
    global g_temp_filename

    full_command = 'curl ' + url_string + ' > ' + g_temp_filename
    subprocess.call(full_command,shell=True)


def extract_test_results():
    """
    This method will scrape the console output for pyunit,runit and hadoop runs and grab the list of failed tests.
    It will then attempt to update the failed tests dictionary to keep track of when failure occurs to see if a test
    is qualified as an intermittent.

    To find the failed tests, search for the text of "All Failed Test".  Lines after will contain the failed
    tests.  Stop when you find "All Tests"
    1. For each test found, update the text file describing all failed tests.
    2. Update the dictionary containing failed tests
    3. Locate the text file of failed tests and concatenate the failed tests messages to one spot.

    :return: none
    """

    found_failed_test = False
    if os.path.isfile(g_temp_filename):
        console_file = open(g_temp_filename,'r')  # open temp file that stored jenkins job console output

        for each_line in console_file:  # go through each line of console output to extract build ID, data/time ...
            each_line.strip()
            print(each_line)
            if ("Test Result" in each_line) and ("failure" in each_line): # the next few lines will contain failed tests
                temp = each_line.split("testReport")
                if ("Test Result" in temp[1]) and ("failure" in temp[1]):        # grab number of failed tests
                    tempCount = int(temp[1].split("</a>")[1].split(" ")[0].split("(")[1])

                    if isinstance(tempCount, int) and tempCount > 0:  # temp[1], temp[2],... should contain failed tests
                        for findex in range(2,len(temp)):
                            tempMess = temp[findex].split(">")
                            failed_message_path = tempMess[0].strip('"')
                            fname = tempMess[1].strip("</a").strip("r_suite.")  # put to output summary test fiile


                print("wow")


            if "All Tests" in each_line:        # end of useful information for us here
                break                           # quit now.


        console_file.close()

def main(argv):
    """
    Main program.  Expect script name plus 7 inputs in the following order:
    - This script name
    1. timestamp: date +'%m_%d_%Y_%H:%M:%S:%3N' or GIT_DATE
    2. jenkins_job_name (JOB_NAME)
    3. build_id (BUILD_ID)
    4. git hash (GIT_COMMIT)
    5. node name (NODE_NAME)
    6. unit test category (JUnit, PyUnit, RUnit, Hadoop)
    7. Jenkins URL (JENKINS_URL)
    8. Text file name where failure summaries are stored

    @return: none
    """
    global g_script_name
    global g_test_root_dir
    global g_timestamp
    global g_job_name
    global g_build_id
    global g_git_hash
    global g_node_name
    global g_unit_test_type
    global g_jenkins_url
    global g_temp_filename
    global g_summary_text_filename


    # global g_output_filename_failed_tests
    # global g_output_filename_passed_tests
    # global g_output_pickle_filename
    # global g_failure_occurred
    # global g_failed_test_info_dict
    # global g_java_message_pickle_filename



    if len(argv) < 9:
        print "Wrong call.  Not enough arguments.\n"
        usage()
        sys.exit(1)
    else:   # we may be in business
        g_script_name = os.path.basename(argv[0])   # get name of script being run.
        g_timestamp = argv[1]
        g_job_name = argv[2]
        g_build_id = argv[3]
        g_git_hash = argv[4]
        g_node_name= argv[5]
        g_unit_test_type = argv[6]
        g_jenkins_url = argv[7]

        g_temp_filename = os.path.join(g_test_root_dir,'tempText')
        g_summary_text_filename = os.path.join(g_test_root_dir, argv[8])
        resource_url = '/'.join([g_jenkins_url, "job", g_job_name, g_build_id, "#showFailuresLink/"])
        get_console_out(resource_url)       # save remote console output in local directory
        extract_test_results()      # grab the console text and stored the failed tests.


        # g_temp_filename = os.path.join(g_test_root_dir,'tempText')
        # g_summary_text_filename = os.path.join(g_test_root_dir,argv[2])
        #
        #
        # extract_job_build_url(resource_url) # extract the job name of build id for identification purposes
        #
        # log_filename = g_failed_test_info_dict["1.jobName"]
        # log_pickle_filename = g_failed_test_info_dict["1.jobName"]
        #
        # # pickle file that store bad Java messages that we can ignore.
        # g_java_message_pickle_filename = os.path.join(g_test_root_dir,g_java_message_pickle_filename)
        # g_output_filename_failed_tests = os.path.join(g_test_root_dir,log_filename)
        # g_output_filename_passed_tests = os.path.join(g_test_root_dir,log_filename)
        # g_output_pickle_filename = os.path.join(g_test_root_dir,log_pickle_filename)
        #
        # load_java_messages_to_ignore()          # load in bad java messages to ignore and store in g_ok_java_messages
        #
        # extract_java_messages()     # grab dangerous java messages that we found for the various unit tests
        # if ((len(g_failed_jobs) > 0) or (g_failed_test_info_dict["7.build_failure"]=='Yes')):
        #     g_failure_occurred = True
        #
        # if g_failure_occurred:
        #     save_dict() # save the dict structure in a pickle file and a text file when failure is detected
        #     update_summary_file()   # join together all log files into one giant summary text.
        #
        #     # output this info to console to form the list of failed jenkins jobs.
        #     print g_failed_test_info_dict["1.jobName"]+' build '+g_failed_test_info_dict["2.build_id"]+','
        # else:
        #     print ""


if __name__ == "__main__":
    main(sys.argv)

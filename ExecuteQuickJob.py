import sys
import logging
from GeneralAgent import GeneralAgent
import data_config as dcfg
from DataUtils import format_string_for_insert, _execute_sql_no_results, _execute_sql
from CommonUtils import get_log_path


# Create a logger
logger = logging.getLogger('quickjob_exe_logger')
logger.setLevel(logging.DEBUG)  # Set minimum logging level


def AddJobLog(job_id, message):
    insert_sql = dcfg.SQL_INSERT_QUICK_JOB_LOG.replace('{job_id}', str(job_id)).replace('{message}', format_string_for_insert(message))
    
    print(86 * '-')
    print(insert_sql)
    print(86 * '-')
    
    return _execute_sql_no_results(insert_sql)


def GetActiveQuickJobs(job_id=None):
    try:
        if job_id is None:
            df = _execute_sql(dcfg.SQL_SELECT_ALL_QUICK_JOBS_EXE)
        else:
            df = _execute_sql(dcfg.SQL_SELECT_QUICK_JOB_EXE.replace('{job_name}', str(job_id)))
        return df
    except Exception as e:
        print(str(e))
        return None
    

def ExecuteQuickJob(agent_id, ai_system, job_id=None, use_advanced_model=0):
    was_successful = False
    try:
        AddJobLog(job_id, 'Executing job...')
        AddJobLog(job_id, 'Command: ' + ai_system)
        genAgent = GeneralAgent(agent_id)
        response = genAgent.run(ai_system)
        AddJobLog(job_id, 'Response: ' + response)
        status = 'success'
        if status == 'success':
            was_successful = True
            AddJobLog(job_id, 'Job completed successfully')
        else:
            AddJobLog(job_id, 'Job did not complete successfully')
    except Exception as e:
        print(str(e))
        AddJobLog(job_id, str(e))
        was_successful = False
    return was_successful


def RunMonitoringQuickJob(job_id=None):
    try:
        print('Querying active quick jobs...')
        logging.info('Querying active quick jobs...')
        df = GetActiveQuickJobs(job_id)

        if df is not None:
            print(df.head())

            # Execute each job
            for index, row in df.iterrows():
                print(index, 'Executing:', row['description'])
                logging.debug('Executing:' + str(row['description']))
                was_successful = ExecuteQuickJob(row['agent_id'], row['ai_system'], job_id=job_id, use_advanced_model=row['use_advanced_model'])
                print('Result:', was_successful)
                logging.debug('Result:' + str(was_successful))
    except Exception as e:
        print(str(e))
        logging.error(str(e))


def Run_QuickJob(job_id):
    # Create handler for logging data to a first file
    handler1 = logging.FileHandler(get_log_path('quickjob_execution_logs.txt'))
    handler1.setLevel(logging.DEBUG)
    formatter1 = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler1.setFormatter(formatter1)

    # Add handler to the logger
    logger.addHandler(handler1)

    # C:/Users/joliver/.conda/envs/aimonitor/python.exe e:/Apps/AIMonitor/BIMonitor_ERP_Outbox.py
    print('Running QuickJob...')
    logger.info('Running QuickJob...')
    try:
        RunMonitoringQuickJob(job_id)
    except Exception as e:
        print(str(e))
        logger.error(str(e))
    print('Finished')
    logger.info('Finished.')


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Please provide one integer parameter.")
        logger.error("Invalid parameter, must be an integer value.")
    else:
        try:
            p_job_id = int(sys.argv[1])
            logger.info("Executing scheduled QuickJob: " + str(p_job_id))
            Run_QuickJob(p_job_id)
            logger.info("Exiting.")
        except Exception as e:
            print(str(e))
            logger.error(str(e))
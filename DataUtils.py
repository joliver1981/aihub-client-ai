import pyodbc
import config as cfg
import data_config as dcfg
import pandas as pd
import logging
from datetime import datetime
from collections import defaultdict
import ast
import yaml
import os
import json
from CommonUtils import get_db_connection, generate_connection_string, get_db_connection_string
from typing import Optional, Dict, Tuple

# logging.basicConfig(filename=cfg.LOG_DIR_DATA, level=logging.DEBUG, format='%(asctime)s [%(levelname)s] - %(message)s')

database_server = cfg.DATABASE_SERVER
database_name = cfg.DATABASE_NAME
username = cfg.DATABASE_UID
password = cfg.DATABASE_PWD


def _execute_sql_no_results(sql_query):
    try:
        #logging.debug('Function: _execute_sql_no_results')
        #logging.debug('SQL Statement: ' + str(sql_query))
        # Establish a connection to SQL Server
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        )

        # Create a cursor object to interact with the database
        cursor = conn.cursor()

        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Execute the SQL query
        cursor.execute(sql_query)

        conn.commit()

        cursor.close()
        
        return True
    except Exception as e:
        print(f"Error: {str(e)}")
        return False
    

def _execute_sql(sql_query):
    try:
        # Establish a connection to SQL Server
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        )

        conn.cursor().execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        df = pd.read_sql_query(sql_query, conn)

        return df
    except Exception as e:
        print(f"Error: {str(e)}")
        return None
    finally:
        conn.close()


def dataframe_to_json(df):
    """
    Convert a Pandas DataFrame to a JSON object.

    Args:
        df (pd.DataFrame): The DataFrame to be converted.

    Returns:
        str: A JSON object as a string.
    """
    json_object = df.to_json(orient='records', date_format='iso', default_handler=str)
    return json_object


def Get_Job(job_id=None):
    print('Executing Get_Job...')
    if job_id is None:
        df = _execute_sql(dcfg.SQL_SELECT_ALL_JOBS)
    else:
        df = _execute_sql(dcfg.SQL_SELECT_JOB.replace('{job_name}', job_id))
    return df


def Get_Collection(collection_id=None):
    print('Executing Get_Collection...')
    if collection_id is None:
        df = _execute_sql(dcfg.SQL_SELECT_ALL_COLLECTIONS)
    else:
        df = _execute_sql(dcfg.SQL_SELECT_COLLECTION.replace('{collection_id}', collection_id))
    return df


def Get_QuickJob_Schedule(id=None):
    print('Executing Get_QuickJob_Schedule...')
    if id is None:
        df = _execute_sql(dcfg.SQL_SELECT_QUICK_JOB_SCHEDULES)
    else:
        df = _execute_sql(dcfg.SQL_SELECT_QUICK_JOB_SCHEDULE.replace('{job_id}', id))
    return df


def Get_Connection(connection_id=None):
    print('Executing Get_Connection...')
    if connection_id is None:
        df = _execute_sql(dcfg.SQL_SELECT_ALL_CONNECTIONS)
    else:
        df = _execute_sql(dcfg.SQL_SELECT_CONNECTION.replace('{connection_id}', connection_id))
    return df


def get_connection_password_by_id(connection_id):
    """Fetch just the password field for a connection."""
    try:
        df = _execute_sql(f"SELECT password FROM Connections WHERE id = {connection_id}")
        if not df.empty:
            return df.iloc[0]['password']
        return None
    except Exception as e:
        return None


def update_connection_password_only(connection_id, new_password):
    """Update just the password field after getting new ID."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context for RLS
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("UPDATE Connections SET password = ? WHERE id = ?", (new_password, connection_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f'Error updating connection password reference. {str(e)}')
        return False
    

def Get_Users(user_id=None):
    print('Executing Get_User...')
    if user_id is None:
        df = _execute_sql(dcfg.SQL_SELECT_ALL_USERS)
    else:
        df = _execute_sql(dcfg.SQL_SELECT_USER.replace('{user_id}', user_id))
    return df


def Get_User_by_user_name(user_name):
    print('Executing Get_User by user name...')
    try:
        df = _execute_sql(dcfg.SQL_SELECT_USER_BY_USER_NAME.replace('{user_name}', format_string_for_insert(user_name)))
    except:
        return None
    return df.iloc[0]


def Get_Logs(date):
    print('Executing Get_Log...')
    df = _execute_sql(dcfg.SQL_SELECT_LOG.replace('{date}', format_string_for_insert(date)))
    return df


def Get_QuickJob_Logs(job_id, date, timezone_offset_minutes=0):
    try:
        print('Executing Get_QuickJob_Logs...')
        logging.info('Executing Get_QuickJob_Logs...')
        sql = dcfg.SQL_SELECT_QUICKJOB_LOG.replace('{job_id}', str(job_id)).replace('{date}', format_string_for_insert(date)).replace('{timezone_offset_minutes}', str(timezone_offset_minutes))
        logging.info(sql)
        df = _execute_sql(sql)
    except Exception as e:
        print(str(e))
        logging.error(str(e))
        df = None
    return df


def Get_Job_Desc(job_id):
    current_job_df = Get_Job(job_id=job_id)
    job_desc = ''
    for index, row in current_job_df.iterrows():
        job_desc = row['description']

    return job_desc


def Get_Job_Detail(job_id):
    current_job_df = Get_Job(job_id=job_id)
    job_desc = ''
    ai_system = ''
    ai_prompt = ''
    enabled = ''
    fn_type = ''
    fn_text = ''
    fn_pass_type = ''
    fn_pass_text = ''
    fn_fail_type = ''
    fn_fail_text = ''
    fn_finish_type = ''
    fn_finish_text = ''
    id = ''

    for index, row in current_job_df.iterrows():
        id = row['id']
        job_desc = row['description']
        ai_system = row['ai_system']
        ai_prompt = row['ai_prompt']
        enabled = row['enabled']  #True if row['enabled'] == '1' else False
        fn_type = row['fn_type']
        fn_text = row['fn_text']

        ### FUTURE ###
        fn_pass_type = row['fn_pass_type']
        fn_pass_text = row['fn_pass_text']
        fn_fail_type = row['fn_fail_type']
        fn_fail_text = row['fn_fail_text']
        ### FUTURE ###

        fn_finish_type = row['fn_finish_type']
        fn_finish_text = row['fn_finish_text']

    return id, job_desc, ai_system, ai_prompt, enabled, fn_type, fn_text, fn_finish_type, fn_finish_text, fn_pass_type, fn_pass_text, fn_fail_type, fn_fail_text


def Get_Max_Job_ID():
    return _execute_sql(dcfg.SQL_MAX_JOB_ID)


def Get_Max_Quick_Job_ID():
    return _execute_sql(dcfg.SQL_MAX_QUICK_JOB_ID)


def Get_Max_Quick_Job_Schedule_ID():
    return _execute_sql(dcfg.SQL_MAX_QUICK_JOB_SCHEDULE_ID)


def Get_Max_Collection_ID():
    return _execute_sql(dcfg.SQL_MAX_COLLECTION_ID)


def Get_Max_User_ID():
    return _execute_sql(dcfg.SQL_MAX_USER_ID)


def Get_Max_Connection_ID():
    return _execute_sql(dcfg.SQL_MAX_CONNECTION_ID)


def format_string_for_insert(input_string):
    output_string = "'" + str(input_string).replace("'", "''") + "'"
    return output_string


def Add_Collection(collection_id, collection_name):
    print('Executing Add_Collection...')
    logging.info('Executing Add_Collection...')
    try:
        logging.debug(str(dcfg.SQL_MERGE_COLLECTION.replace('{collection_id}', str(collection_id)).replace('{collection_name}', format_string_for_insert(collection_name))))
        result = _execute_sql_no_results(dcfg.SQL_MERGE_COLLECTION.replace('{collection_id}', str(collection_id)).replace('{collection_name}', format_string_for_insert(collection_name)))

        if str(collection_id) == '0' and result:
            collection_id_df = Get_Max_Collection_ID()

            for index, row in collection_id_df.iterrows():
                collection_id = row['collection_id']
    except Exception as e:
        logging.error(str(e))
        result = False

    return collection_id, result


def Delete_Collection(collection_id):
    logging.info('Call to Delete_Collection...')

    print('Delete collection data...', 'collection_id: ', collection_id)
    insert_sql = dcfg.SQL_DELETE_COLLECTION.replace('{collection_id}', str(collection_id))

    print(86 * '-')
    logging.debug('SQL:' + insert_sql)
    print(insert_sql)
    print(86 * '-')
    
    result = _execute_sql_no_results(insert_sql)

    logging.debug('RESULT:', str(result))

    return result


def Delete_Connection(connection_id):
    logging.info('Call to Delete_Connection...')

    print('Delete connection data...', 'connection_id: ', connection_id)
    insert_sql = dcfg.SQL_DELETE_CONNECTION.replace('{connection_id}', str(connection_id))

    print(86 * '-')
    logging.debug('SQL:' + insert_sql)
    print(insert_sql)
    print(86 * '-')
    
    result = _execute_sql_no_results(insert_sql)

    logging.debug('RESULT:', str(result))

    return result


def Add_User(user_id, user_name, role, email, phone, name, password, auth_provider='local', external_id=''):
    print('Executing Add_User...')
    logging.info('Executing Add_User...')
    try:
        sql_string = dcfg.SQL_MERGE_USER.replace('{user_id}', str(user_id)).replace('{user_name}', format_string_for_insert(user_name)).replace('{role}', str(role)).replace('{email}', format_string_for_insert(email)).replace('{phone}', format_string_for_insert(phone)).replace('{name}', format_string_for_insert(name)).replace('{password}', format_string_for_insert(password)).replace('{auth_provider}', format_string_for_insert(auth_provider)).replace('{external_id}', format_string_for_insert(external_id))
        logging.debug(str(sql_string))
        result = _execute_sql_no_results(sql_string)

        if str(user_id) == '0' and result:
            user_id_df = Get_Max_User_ID()

            for index, row in user_id_df.iterrows():
                user_id = row['id']
    except Exception as e:
        logging.error(str(e))
        result = False

    return user_id, result


def Delete_User(user_id):
    logging.info('Call to Delete_User...')

    print('Delete user data...', 'user_id: ', user_id)
    insert_sql = dcfg.SQL_DELETE_USER.replace('{user_id}', str(user_id))

    print(86 * '-')
    logging.debug('SQL:' + insert_sql)
    print(insert_sql)
    print(86 * '-')
    
    result = _execute_sql_no_results(insert_sql)

    logging.debug('RESULT:', str(result))

    return result


# ============================================================
# Identity Provider Configuration CRUD
# ============================================================

def Get_Identity_Providers(provider_id=None, provider_type=None, enabled_only=False):
    """Fetch identity provider configurations from the database."""
    print('Executing Get_Identity_Providers...')
    try:
        if enabled_only:
            df = _execute_sql(dcfg.SQL_SELECT_ENABLED_IDENTITY_PROVIDERS)
        elif provider_id is not None:
            df = _execute_sql(dcfg.SQL_SELECT_IDENTITY_PROVIDER.replace('{provider_id}', str(provider_id)))
        elif provider_type is not None:
            df = _execute_sql(dcfg.SQL_SELECT_IDENTITY_PROVIDERS_BY_TYPE.replace('{provider_type}', format_string_for_insert(provider_type)))
        else:
            df = _execute_sql(dcfg.SQL_SELECT_ALL_IDENTITY_PROVIDERS)
        return df
    except Exception as e:
        logging.error(f'Error in Get_Identity_Providers: {str(e)}')
        return None


def Save_Identity_Provider(provider_id, provider_type, provider_name, is_enabled, is_default,
                           config_json, auto_provision=1, default_role=1, group_role_mapping='{}'):
    """Create or update an identity provider configuration."""
    print('Executing Save_Identity_Provider...')
    logging.info('Executing Save_Identity_Provider...')
    try:
        sql_string = dcfg.SQL_MERGE_IDENTITY_PROVIDER \
            .replace('{provider_id}', str(provider_id)) \
            .replace('{provider_type}', format_string_for_insert(provider_type)) \
            .replace('{provider_name}', format_string_for_insert(provider_name)) \
            .replace('{is_enabled}', str(1 if is_enabled else 0)) \
            .replace('{is_default}', str(1 if is_default else 0)) \
            .replace('{config_json}', format_string_for_insert(config_json)) \
            .replace('{auto_provision}', str(1 if auto_provision else 0)) \
            .replace('{default_role}', str(default_role)) \
            .replace('{group_role_mapping}', format_string_for_insert(group_role_mapping))
        logging.debug(str(sql_string))
        result = _execute_sql_no_results(sql_string)
        return result
    except Exception as e:
        logging.error(f'Error in Save_Identity_Provider: {str(e)}')
        return False


def Delete_Identity_Provider(provider_id):
    """Delete an identity provider configuration."""
    print('Executing Delete_Identity_Provider...')
    logging.info('Executing Delete_Identity_Provider...')
    try:
        sql_string = dcfg.SQL_DELETE_IDENTITY_PROVIDER.replace('{provider_id}', str(provider_id))
        result = _execute_sql_no_results(sql_string)
        return result
    except Exception as e:
        logging.error(f'Error in Delete_Identity_Provider: {str(e)}')
        return False


def Add_Connection(connection_id, connection_name, server, port, database_name, database_type, user_name, password, parameters, connection_string, odbc_driver='', instance_url='', token='', api_key='', dsn=''):
    print('Executing Add_Connection...')
    logging.info('Executing Add_Connection...')
    try:
        merge_sql = dcfg.SQL_MERGE_CONNECTION.replace('{instance_url}', format_string_for_insert(instance_url or '')).replace('{token}', format_string_for_insert(token or '')).replace('{api_key}', format_string_for_insert(api_key or '')).replace('{dsn}', format_string_for_insert(dsn or '')).replace('{odbc_driver}', format_string_for_insert(odbc_driver or '')).replace('{port}', str(port or '0')).replace('{database_type}', format_string_for_insert(database_type)).replace('{connection_string}', format_string_for_insert(connection_string)).replace('{parameters}', format_string_for_insert(parameters)).replace('{connection_id}', str(connection_id)).replace('{connection_name}', format_string_for_insert(connection_name)).replace('{server}', format_string_for_insert(server)).replace('{database_name}', format_string_for_insert(database_name)).replace('{user_name}', format_string_for_insert(user_name)).replace('{password}', format_string_for_insert(password))

        logging.debug(str(merge_sql))
        print(str(merge_sql))
        result = _execute_sql_no_results(merge_sql)

        if str(connection_id) == '0' and result:
            connection_id = Get_Max_Connection_ID()

            for index, row in connection_id.iterrows():
                connection_id = row['connection_id']
    except Exception as e:
        logging.error(str(e))
        result = False

    return connection_id, result


def Add_Job_Header(job_id, job_desc, ai_system, ai_prompt, enabled, collection_id, pass_fail):
    new_job_id = None

    if enabled:
        int_enabled = 1
    else:
        int_enabled = 0

    if pass_fail:
        int_pass_fail = 1
    else:
        int_pass_fail = 0

    if job_id == '' or job_id is None:
        job_id = 0

    print('Merging job data...', 'JOBID: ', job_id)
    insert_sql = dcfg.SQL_MERGE_JOB_HEADER.replace('{job_id}', str(job_id)).replace('{job_desc}', format_string_for_insert(job_desc)).replace('{ai_system}', format_string_for_insert(ai_system)).replace('{ai_prompt}', format_string_for_insert(ai_prompt)).replace('{enabled}', format_string_for_insert(str(int_enabled))).replace('{collection_id}', str(collection_id)).replace('{pass_fail}', format_string_for_insert(str(int_pass_fail)))

    #if job_id == '':
        #print('Adding new job...')
        #insert_sql = dcfg.SQL_INSERT_JOB_HEADER.replace('{job_desc}', format_string_for_insert(job_desc)).replace('{ai_system}', format_string_for_insert(ai_system)).replace('{ai_prompt}', format_string_for_insert(ai_prompt)).replace('{enabled}', format_string_for_insert(str(int_enabled)))

    print(86 * '-')
    print(insert_sql)
    print(86 * '-')
    
    result = _execute_sql_no_results(insert_sql)

    if job_id == '' or job_id is None or job_id <= 0:
        new_job_id_df = Get_Max_Job_ID()

        for index, row in new_job_id_df.iterrows():
            new_job_id = row['job_id']
    else:
        new_job_id = job_id

    print('Done.', 'NEW JOBID: ', job_id)

    return new_job_id


def Add_Job_Detail(job_id, fn_type, fn_text, fn_finish_type, fn_finish_text):
    insert_sql = dcfg.SQL_MERGE_JOB_DETAIL.replace('{job_id}', str(job_id)).replace('{fn_type}', format_string_for_insert(fn_type)).replace('{fn_text}', format_string_for_insert(fn_text)).replace('{fn_finish_type}', format_string_for_insert(fn_finish_type)).replace('{fn_finish_text}', format_string_for_insert(fn_finish_text))
    
    print(86 * '-')
    print(insert_sql)
    print(86 * '-')
    
    return _execute_sql_no_results(insert_sql)


def Add_Job(job_id, job_desc, ai_system, ai_prompt, enabled, fn_type, fn_text, fn_pass_type, fn_pass_text, fn_fail_type, fn_fail_text, fn_finish_type, fn_finish_text, collection_id, pass_fail):
    INSERT_SUCCESSFUL = False

    if job_id is None:
        job_id = 0

    # Add job header
    print(86 * '=')
    print(job_desc)
    print(ai_system)
    print(ai_prompt)
    print(enabled)
    print(86 * '=')
    new_job_id = Add_Job_Header(job_id, job_desc, ai_system, ai_prompt, enabled, collection_id, pass_fail)

    print(new_job_id)
    print(type(new_job_id))
    print(86 * '=')

    # Add job detail
    if new_job_id is not None:
        insert_result = Add_Job_Detail(new_job_id, fn_type, fn_text, fn_finish_type, fn_finish_text)

        if insert_result:
            INSERT_SUCCESSFUL = True

    return INSERT_SUCCESSFUL


def Get_Quick_Job_DF(user_id, job_id=None):
    if job_id is None:
        df = _execute_sql(dcfg.SQL_SELECT_ALL_QUICK_JOBS.replace('{user_id}', format_string_for_insert(user_id)))
    else:
        df = _execute_sql(dcfg.SQL_SELECT_QUICK_JOB.replace('{job_name}', job_id).replace('{user_id}', format_string_for_insert(user_id)))
    return df


def Get_Quick_Job(job_id):
    job_desc = ''
    ai_system = ''
    enabled = ''
    collection_id = ''
    id = ''

    if job_id is None:
        df = _execute_sql(dcfg.SQL_SELECT_ALL_QUICK_JOBS)
    else:
        df = _execute_sql(dcfg.SQL_SELECT_QUICK_JOB.replace('{job_name}', job_id))

    for index, row in df.iterrows():
        id = row['id']
        job_desc = row['description']
        ai_system = row['ai_system']
        enabled = row['enabled']  #True if row['enabled'] == '1' else False
        collection_id = row['collection_id']

    return id, job_desc, ai_system, enabled, collection_id


def Add_Quick_Job(job_id, job_desc, ai_system, enabled, collection_id, agent_id):
    logging.info('Call to Add_Quick_Job...')
    new_job_id = None

    # if enabled:
    #     int_enabled = 1
    # else:
    #     int_enabled = 0

    if job_id == '' or job_id is None or job_id == '0':
        job_id = 0

    print('Merging quick job data...', 'JOBID: ', job_id)
    insert_sql = dcfg.SQL_MERGE_QUICK_JOB.replace('{agent_id}', str(agent_id)).replace('{job_id}', str(job_id)).replace('{job_desc}', format_string_for_insert(job_desc)).replace('{ai_system}', format_string_for_insert(ai_system)).replace('{enabled}', str(enabled)).replace('{collection_id}', str(collection_id))

    print(86 * '-')
    logging.debug('SQL:' + insert_sql)
    print(insert_sql)
    print(86 * '-')
    
    result = _execute_sql_no_results(insert_sql)

    #logging.debug('RESULT:', str(result))

    if job_id == '' or job_id is None or job_id == 0:
        new_job_id_df = Get_Max_Quick_Job_ID()

        for index, row in new_job_id_df.iterrows():
            new_job_id = row['job_id']
    else:
        new_job_id = job_id

    print('Done.', 'NEW JOBID: ', job_id)
    logging.info('Finished updating job:' + str(job_id))

    return new_job_id


def select_user_agents_and_tools(user_id, user_role=None):
    try:
        # Establish the connection
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        )
        cursor = conn.cursor()

        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Query to select all agents and their tools
        # Admins (role 3) see all agents
        if user_role >= 3:
            cursor.execute("""
                SELECT 
                    a.id as agent_id,
                    a.description as agent_description,
                    a.objective as agent_objective,
                    a.enabled as agent_enabled,
                    a.create_date as agent_create_date,
                    t.tool_name,
                    t.custom_tool
                FROM 
                    [dbo].[Agents] a
                LEFT JOIN 
                    [dbo].[AgentTools] t ON a.id = t.agent_id
                WHERE a.[is_data_agent] = 0
                ORDER BY 
                    a.id, t.tool_name
            """)
        else:
            cursor.execute("""
                SELECT 
                    a.id as agent_id,
                    a.description as agent_description,
                    a.objective as agent_objective,
                    a.enabled as agent_enabled,
                    a.create_date as agent_create_date,
                    t.tool_name,
                    t.custom_tool
                FROM 
                    [dbo].[Agents] a
                LEFT JOIN 
                    [dbo].[AgentTools] t ON a.id = t.agent_id
                WHERE a.id IN (SELECT DISTINCT a.id 
                                FROM Agents a 
                                JOIN AgentGroups ag ON a.id = ag.agent_id 
                                JOIN UserGroups ug ON ag.group_id = ug.group_id 
                                WHERE ug.user_id = ? AND a.enabled = 1) 
                AND a.[is_data_agent] = 0
                ORDER BY 
                    a.id, t.tool_name
            """, user_id)

        # Fetch all results
        rows = cursor.fetchall()

        # Process results into a list of dictionaries
        agents = defaultdict(lambda: {'agent_description': '', 'agent_enabled': '', 'agent_create_date': '', 'tool_names': [], 'custom_tool': []})

        for row in rows:
            agent_id = row.agent_id
            if row.agent_description not in agents[agent_id]:
                agents[agent_id]['agent_description'] = row.agent_description
                agents[agent_id]['agent_objective'] = row.agent_objective
                agents[agent_id]['agent_enabled'] = row.agent_enabled
                agents[agent_id]['agent_create_date'] = row.agent_create_date
            if row.tool_name:
                agents[agent_id]['tool_names'].append(row.tool_name)
                agents[agent_id]['custom_tool'].append(row.custom_tool)

        result = [{'agent_id': agent_id, **data} for agent_id, data in agents.items()]

        print(result)

        return result

    except Exception as e:
        print(f"Error: {e}")
        return None

    finally:
        # Close the connection
        cursor.close()
        conn.close()


def select_all_agents_and_tools():
    try:
        # Establish the connection
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        )
        cursor = conn.cursor()

        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Query to select all agents and their tools
        cursor.execute("""
            SELECT 
                a.id as agent_id,
                a.description as agent_description,
                a.objective as agent_objective,
                a.enabled as agent_enabled,
                a.create_date as agent_create_date,
                t.tool_name,
                t.custom_tool
            FROM 
                [dbo].[Agents] a
            LEFT JOIN 
                [dbo].[AgentTools] t ON a.id = t.agent_id
            WHERE a.[is_data_agent] = 0
            ORDER BY 
                a.id, t.tool_name
        """)

        # Fetch all results
        rows = cursor.fetchall()

        # Process results into a list of dictionaries
        agents = defaultdict(lambda: {'agent_description': '', 'agent_enabled': '', 'agent_create_date': '', 'tool_names': [], 'custom_tool': []})

        for row in rows:
            agent_id = row.agent_id
            if row.agent_description not in agents[agent_id]:
                agents[agent_id]['agent_description'] = row.agent_description
                agents[agent_id]['agent_objective'] = row.agent_objective
                agents[agent_id]['agent_enabled'] = row.agent_enabled
                agents[agent_id]['agent_create_date'] = row.agent_create_date
            if row.tool_name:
                agents[agent_id]['tool_names'].append(row.tool_name)
                agents[agent_id]['custom_tool'].append(row.custom_tool)

        result = [{'agent_id': agent_id, **data} for agent_id, data in agents.items()]

        #print(result)

        return result

    except Exception as e:
        print(f"Error: {e}")
        return None

    finally:
        # Close the connection
        cursor.close()
        conn.close()


def select_all_agents_and_connections():
    try:
        query = """
            SELECT 
                a.id as agent_id,
                a.description as agent_description,
                a.objective as agent_objective,
                a.enabled as agent_enabled,
                a.create_date as agent_create_date,
                c.id connection_id,
                c.[connection_name] connection_name
            FROM 
                [dbo].[Agents] a
            LEFT JOIN 
                [dbo].[AgentConnections] t ON a.id = t.agent_id
            LEFT JOIN [dbo].[Connections] c ON c.id = t.connection_id
            WHERE a.[is_data_agent] = 1
        """
        df = _execute_sql(query)

        return df
    except Exception as e:
        print(f"Error: {e}")
        return None


def select_all_database_connections():
    try:
        query = """
            SELECT [id] [connection_id]
                ,[connection_name]
                ,[server]
                ,[database_name]
                ,[database_type]
            FROM [dbo].[Connections]
        """
        df = _execute_sql(query)

        return df
    except Exception as e:
        print(f"Error: {e}")
        return None


def select_user_agents_and_connections(user_id, user_role=None):
    try:
        # Admins (role 3) see all agents
        if user_role >= 3:
            query = """
                SELECT 
                    a.id as agent_id,
                    a.description as agent_description,
                    a.objective as agent_objective,
                    a.enabled as agent_enabled,
                    a.create_date as agent_create_date,
                    c.id connection_id,
                    c.[connection_name] connection_name
                FROM 
                    [dbo].[Agents] a
                LEFT JOIN 
                    [dbo].[AgentConnections] t ON a.id = t.agent_id
                LEFT JOIN [dbo].[Connections] c ON c.id = t.connection_id
                WHERE a.[is_data_agent] = 1
            """
        else:
            query = f"""
                SELECT 
                    a.id as agent_id,
                    a.description as agent_description,
                    a.objective as agent_objective,
                    a.enabled as agent_enabled,
                    a.create_date as agent_create_date,
                    c.id connection_id,
                    c.[connection_name] connection_name
                FROM 
                    [dbo].[Agents] a
                LEFT JOIN 
                    [dbo].[AgentConnections] t ON a.id = t.agent_id
                LEFT JOIN [dbo].[Connections] c ON c.id = t.connection_id
                WHERE a.[is_data_agent] = 1
                AND a.id IN (SELECT DISTINCT a.id 
                                FROM Agents a 
                                JOIN AgentGroups ag ON a.id = ag.agent_id 
                                JOIN UserGroups ug ON ag.group_id = ug.group_id 
                                WHERE ug.user_id = {user_id} AND a.enabled = 1) 
            """

        df = _execute_sql(query)

        return df
    except Exception as e:
        print(f"Error: {e}")
        return None


def get_agent_ids():
    try:
        # Establish the connection
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        )
        cursor = conn.cursor()

        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Query to select all agents and their tools
        cursor.execute("""
            SELECT 
                a.id as agent_id
            FROM 
                [dbo].[Agents] a
            ORDER BY 
                a.id
        """)

        # Fetch all results
        rows = cursor.fetchall()
        result = []
        for row in rows:
            result.append(row.agent_id)
        return result
    except Exception as e:
        print(f"Error: {e}")
        return []
    finally:
        # Close the connection
        cursor.close()
        conn.close()


def get_agent_config(agent_id):
    try:
        # Establish the connection
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        )
        cursor = conn.cursor()

        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Query to select all agents and their tools
        cursor.execute("""
            SELECT 
                a.id as agent_id,
                a.description as agent_description,
                a.objective as agent_objective,
                a.enabled as agent_enabled,
                a.create_date as agent_create_date,
                t.tool_name,
                t.custom_tool
            FROM 
                [dbo].[Agents] a
            LEFT JOIN 
                [dbo].[AgentTools] t ON a.id = t.agent_id
            WHERE a.id = ?
            ORDER BY 
                a.id, t.tool_name
        """, agent_id)

        # Fetch all results
        rows = cursor.fetchall()

        # Process results into a list of dictionaries
        agents = defaultdict(lambda: {'agent_description': '', 'agent_enabled': '', 'agent_create_date': '', 'tool_names': [], 'custom_tool': []})

        for row in rows:
            agent_id = row.agent_id
            if row.agent_description not in agents[agent_id]:
                agents[agent_id]['agent_description'] = row.agent_description
                agents[agent_id]['agent_objective'] = row.agent_objective
                agents[agent_id]['agent_enabled'] = row.agent_enabled
                agents[agent_id]['agent_create_date'] = row.agent_create_date
            if row.tool_name:
                agents[agent_id]['tool_names'].append(row.tool_name)
                agents[agent_id]['custom_tool'].append(row.custom_tool)

        result = [{'agent_id': agent_id, **data} for agent_id, data in agents.items()]

        print(result)

        return result

    except Exception as e:
        print(f"Error: {e}")
        return None

    finally:
        # Close the connection
        cursor.close()
        conn.close()


def insert_agent_with_tools(agent_description, agent_objective, agent_enabled, tool_names, core_tool_names):
    try:
        # Establish the connection
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        )

        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Insert into Agents table
        cursor.execute("""
            INSERT INTO [dbo].[Agents] (description, objective, enabled)
            VALUES (?, ?, ?)
            """, agent_description, agent_objective, agent_enabled)
        
        # Get the id of the newly inserted agent
        cursor.execute("SELECT @@IDENTITY AS 'Identity'")
        agent_id = cursor.fetchone()[0]

        # Insert into AgentTools table for each core_tool_name
        for tool_name in core_tool_names:
            cursor.execute("""
                INSERT INTO [dbo].[AgentTools] (agent_id, tool_name, enabled, create_date, [custom_tool])
                VALUES (?, ?, 1, ?, 0)
                """, agent_id, tool_name, datetime.now())

        # Insert into AgentTools table for each tool_name
        for tool_name in tool_names:
            cursor.execute("""
                INSERT INTO [dbo].[AgentTools] (agent_id, tool_name, enabled, create_date, [custom_tool])
                VALUES (?, ?, 1, ?, 1)
                """, agent_id, tool_name, datetime.now())

        # Commit the transaction
        conn.commit()
        
        # Return the newly generated agent_id
        return agent_id
        
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
        return None
        
    finally:
        # Close the connection
        cursor.close()
        conn.close()


def delete_agent(agent_id):
    try:
        # Establish the connection
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        )

        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Clear existing tools
        cursor.execute("DELETE FROM [dbo].[AgentTools] WHERE agent_id = ?", agent_id)

        # Clear agent
        cursor.execute("DELETE FROM [dbo].[Agents] WHERE id = ?", agent_id)

        # Commit the transaction
        conn.commit()

        return True
    except Exception as e:
        print(str(e))
        return False

    finally:
        # Close the connection
        cursor.close()
        conn.close()


def update_agent_with_tools(agent_id, agent_description, agent_objective, agent_enabled, tool_names, core_tool_names):
    try:
        # Establish the connection
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        )

        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        print('Executing update:', f"""
            UPDATE [dbo].[Agents] 
            SET description={agent_description}, objective={agent_objective}, enabled={agent_enabled} 
            WHERE id = {agent_id}
            """)
        # Insert into Agents table
        cursor.execute(f"""
            UPDATE [dbo].[Agents] 
            SET description=?, objective=?, enabled=? 
            WHERE id = ?
            """, agent_description, agent_objective, agent_enabled, agent_id)
        
        # Clear existing tools
        cursor.execute("DELETE FROM [dbo].[AgentTools] WHERE agent_id = ?", agent_id)

        # Insert into AgentTools table for each custom core_tool_names
        for tool_name in core_tool_names:
            cursor.execute("""
                INSERT INTO [dbo].[AgentTools] (agent_id, tool_name, enabled, create_date, [custom_tool])
                VALUES (?, ?, 1, ?, 0)
                """, agent_id, tool_name, datetime.now())

        # Insert into AgentTools table for each custom tool_name
        for tool_name in tool_names:
            cursor.execute("""
                INSERT INTO [dbo].[AgentTools] (agent_id, tool_name, enabled, create_date, [custom_tool])
                VALUES (?, ?, 1, ?, 1)
                """, agent_id, tool_name, datetime.now())

        # Commit the transaction
        conn.commit()
        
        # Return the newly generated agent_id
        return agent_id
        
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
        return None
        
    finally:
        # Close the connection
        cursor.close()
        conn.close()


def insert_agent_with_connection(agent_description, agent_objective, agent_enabled, connection_id):
    try:
        # Establish the connection
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        )

        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Insert into Agents table
        cursor.execute("""
            INSERT INTO [dbo].[Agents] (description, objective, enabled, is_data_agent)
            VALUES (?, ?, ?, 1)
            """, agent_description, agent_objective, agent_enabled)
        
        # Get the id of the newly inserted agent
        cursor.execute("SELECT @@IDENTITY AS 'Identity'")
        agent_id = cursor.fetchone()[0]

        # Insert into AgentConnections table
        cursor.execute("""
            INSERT INTO [dbo].[AgentConnections] (agent_id, connection_id)
            VALUES (?, ?)
            """, agent_id, connection_id)

        # Commit the transaction
        conn.commit()
        
        # Return the newly generated agent_id
        return agent_id
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
        return None
    finally:
        # Close the connection
        cursor.close()
        conn.close()


def update_agent_with_connection(agent_id, agent_description, agent_objective, agent_enabled, connection_id):
    try:
        # Establish the connection
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        )

        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Insert into Agents table
        cursor.execute(f"""
            UPDATE [dbo].[Agents] 
            SET description=?, objective=?, enabled=? 
            WHERE id = ?
            """, agent_description, agent_objective, agent_enabled, agent_id)

        # Clear existing connection
        cursor.execute("DELETE FROM [dbo].[AgentConnections] WHERE agent_id = ?", agent_id)
        
        # Insert into AgentConnections table
        cursor.execute("""
            INSERT INTO [dbo].[AgentConnections] (agent_id, connection_id)
            VALUES (?, ?)
            """, agent_id, connection_id)

        # Commit the transaction
        conn.commit()
        
        # Return the generated agent_id
        return agent_id
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
        return None
    finally:
        # Close the connection
        cursor.close()
        conn.close()


def get_groups(group_id=None):
    print('Executing get groups...')
    if group_id is None:
        df = _execute_sql(dcfg.SQL_SELECT_GROUPS)
    else:
        df = _execute_sql(dcfg.SQL_SELECT_GROUP.replace('{id}', str(group_id)))
    return df


def get_workflows(workflow_id=None):
    print('Executing get workflows...')
    if workflow_id is None:
        df = _execute_sql(dcfg.SQL_SELECT_WORKFLOWS)
    else:
        df = _execute_sql(dcfg.SQL_SELECT_WORKFLOW.replace('{id}', str(workflow_id)))
    return df

def get_workflow_variables(workflow_id):
    print('Executing get workflow variables...')
    df = _execute_sql(dcfg.SQL_SELECT_WORKFLOW_VARIABLES.replace('{id}', str(workflow_id)))
    return df

def delete_workflow(workflow_id):
    print('Executing get workflows...')
    result = _execute_sql_no_results(dcfg.SQL_DELETE_WORKFLOW.replace('{id}', str(workflow_id)))
    return result

def get_workflowCategories(workflow_id=None):
    print('Executing get categories...')
    if workflow_id is None:
        df = _execute_sql(dcfg.SQL_SELECT_WORKFLOW_CATEGORIES)
    else:
        df = _execute_sql(dcfg.SQL_SELECT_WORKFLOW_CATEGORY.replace('{id}', str(workflow_id)))
    return df

def add_group(group_name, id=None):
    try:
        merge_sql = dcfg.SQL_MERGE_GROUPS.replace('{id}', str(id)).replace('{group_name}', format_string_for_insert(group_name))

        # Insert into groups table
        result = _execute_sql_no_results(merge_sql)

        # Get the id of the newly inserted group
        if result:
            if id == 0 or id is None:
                group_id =  _execute_sql("SELECT MAX(id) FROM [dbo].[Groups]")
                group_id = group_id.iloc[0, 0]
            else:
                group_id = id
        else:
            raise('Failed to insert group - SQL error.')
        
        # Return the newly generated id
        return group_id
    except Exception as e:
        print(f"Error: {e}")
        return None
    

def delete_group(id):
    try:
        # Delete group
        result = _execute_sql_no_results(f"DELETE FROM [dbo].[Groups] WHERE id = {id}")

        return result
    except Exception as e:
        print(f"Error: {e}")
        return False
    

def get_user_groups(group_id):
    print('Executing get user groups...')
    df = _execute_sql(dcfg.SQL_SELECT_USER_GROUPS.replace('{id}', group_id))
    return df


def get_user_group_assigned_unassigned(group_id):
    try:
        # Establish the connection
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        )

        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Get all users
        cursor.execute("SELECT id, user_name, name, role FROM [dbo].[User]")
        all_users = cursor.fetchall()

        # Get assigned users for the group
        cursor.execute("SELECT g.user_id, u.user_name, u.name FROM [dbo].[UserGroups] g JOIN [dbo].[User] u on u.[id] = g.[user_id] WHERE group_id = ?", (group_id,))
        assigned_user_ids = [row[0] for row in cursor.fetchall()]

        assigned_users = [user for user in all_users if user[0] in assigned_user_ids]
        unassigned_users = [user for user in all_users if user[0] not in assigned_user_ids]
        return assigned_users, unassigned_users
    except Exception as e:
        print(str(e))
        return None, None
    finally:
        # Close the connection
        cursor.close()
        conn.close()


def get_agent_info():
    try:
        conn = pyodbc.connect(
                f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
            )
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        cursor.execute("SELECT id, description, objective FROM [dbo].[Agents]")
        agents = cursor.fetchall()
        agent_info = [{'id': agent[0], 'description': agent[1], 'objective': agent[2]} for agent in agents]
        return agent_info
    except Exception as e:
        print(str(e))
        agent_info = None
    finally:
        # Close the connection
        cursor.close()
        conn.close()


def is_data_agent(agent_id):
    """Check whether an agent is a data agent (data assistant).

    Returns True if the agent has is_data_agent = 1, False otherwise.
    """
    try:
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        )
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        cursor.execute(
            "SELECT ISNULL(is_data_agent, 0) FROM [dbo].[Agents] WHERE id = ?",
            (int(agent_id),),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return bool(row[0]) if row else False
    except Exception as e:
        print(f"is_data_agent error: {e}")
        return False


def save_permissions(group_id, assigned_users, permissions):
    try:
        conn = pyodbc.connect(
                    f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
                )
        
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        # Save user group assignments
        cursor.execute("DELETE FROM [dbo].[UserGroups] WHERE group_id = ?", (group_id,))
        for user_id in assigned_users:
            cursor.execute("INSERT INTO [dbo].[UserGroups] (group_id, user_id) VALUES (?, ?)", (group_id, user_id))

        # Save group permissions
        cursor.execute("DELETE FROM [dbo].[AgentGroups] WHERE group_id = ?", (group_id,))
        for agent_id in permissions:
            cursor.execute("INSERT INTO [dbo].[AgentGroups] (group_id, agent_id) VALUES (?, ?)", (group_id, agent_id))

        conn.commit()
        return True
    except Exception as e:
        print(str(e))
        return False
    finally:
        # Close the connection
        cursor.close()
        conn.close()


def get_group_permissions(group_id):
    try:
        conn = pyodbc.connect(
                        f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
                    )
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        # Get assigned agent permissions for the group
        cursor.execute("SELECT agent_id FROM [dbo].[AgentGroups] WHERE group_id = ?", (group_id,))

        assigned_permissions = [row[0] for row in cursor.fetchall()]

        return assigned_permissions
    except Exception as e:
        print(str(e))
        return None
    finally:
        # Close the connection
        cursor.close()
        conn.close()


def Delete_Quick_Job(job_id):
    logging.info('Call to Delete_Quick_Job...')

    print('Delete quick job data...', 'JOBID: ', job_id)
    insert_sql = dcfg.SQL_DELETE_QUICK_JOB.replace('{job_id}', str(job_id))

    print(86 * '-')
    logging.debug('SQL:' + insert_sql)
    print(insert_sql)
    print(86 * '-')
    
    result = _execute_sql_no_results(insert_sql)

    logging.debug('RESULT:', str(result))

    print('Done.', 'NEW JOBID: ', job_id)
    logging.info('Finished deleting job:' + str(job_id))

    return result


def Add_Quick_Job_Schedule(id, job_id, task_name, start_time, frequency, enabled):
    logging.info('Call to Add_Quick_Job_Schedule...')
    new_job_id = None

    # if enabled:
    #     int_enabled = 1
    # else:
    #     int_enabled = 0

    if id == '' or id is None or id == '0':
        job_id = 0

    print('Merging quick job schedule data...', 'JOBID: ', job_id)
    insert_sql = dcfg.SQL_MERGE_QUICK_JOB_SCHEDULE.replace('{job_id}', str(job_id)).replace('{task_name}', format_string_for_insert(task_name)).replace('{frequency}', format_string_for_insert(frequency)).replace('{start_time}', format_string_for_insert(start_time)).replace('{enabled}', str(enabled)).replace('{id}', str(id))

    print(86 * '-')
    logging.debug('SQL:' + insert_sql)
    print(insert_sql)
    print(86 * '-')
    
    result = _execute_sql_no_results(insert_sql)

    #logging.debug('RESULT:', str(result))

    if id == '' or id is None or id == 0:
        new_job_id_df = Get_Max_Quick_Job_Schedule_ID()

        for index, row in new_job_id_df.iterrows():
            new_job_id = row['id']
    else:
        new_job_id = id

    print('Done.', 'NEW JOBID: ', job_id)
    logging.info('Finished updating quick job schedule:' + str(new_job_id))

    return new_job_id


def Execute_Query(connection_id, query):
    return True


def Get_Tables(connection_id):
    return _execute_sql(dcfg.SQL_SELECT_TABLES.replace('{connection_id}', str(connection_id)))


def Add_Table(table_id, table_name, table_desc, connection_id):
    new_table_id = None

    if table_id == '' or table_id is None:
        table_id = 0

    print('Merging table data...', 'Table ID: ', table_id)
    logging.info('Merging table data... Table ID:' + str(table_id))
    insert_sql = dcfg.SQL_MERGE_TABLE.replace('{connection_id}', str(connection_id)).replace('{id}', str(table_id)).replace('{table_name}', format_string_for_insert(table_name)).replace('{table_description}', format_string_for_insert(table_desc))

    print(86 * '-')
    print(insert_sql)
    logging.debug(insert_sql)
    print(86 * '-')
    
    # Add/update record with merge
    result = _execute_sql_no_results(insert_sql)

    # If insert/update was successful
    if result:
        if table_id == '' or table_id is None or int(table_id) <= 0:
            df = Get_Max_Table_ID()

            for index, row in df.iterrows():
                new_table_id = row['id']
        else:
            new_table_id = table_id
    else:
        new_table_id = None

    print('Done.', 'NEW Table ID: ', new_table_id)
    logging.info('Done - New Table ID:' + str(new_table_id))

    return new_table_id


def Add_Column(table_id, column_id, column_name, column_description, column_values):
    new_column_id = None

    if column_id == '' or column_id is None:
        column_id = 0

    print('Merging column data...', 'Table ID: ', table_id)
    logging.info('Merging column data... Table ID:' + str(table_id))
    insert_sql = dcfg.SQL_MERGE_COLUMN.replace('{id}', str(column_id)).replace('{table_id}', str(table_id)).replace('{column_name}', format_string_for_insert(column_name)).replace('{column_description}', format_string_for_insert(column_description)).replace('{column_values}', format_string_for_insert(column_values))

    print(86 * '-')
    logging.debug(insert_sql)
    print(insert_sql)
    print(86 * '-')
    
    # Add/update record with merge
    result = _execute_sql_no_results(insert_sql)

    # If insert/update was successful
    if result:
        if column_id == '' or column_id is None or int(column_id) <= 0:
            df = Get_Max_Column_ID()

            for index, row in df.iterrows():
                new_column_id = row['id']
        else:
            new_column_id = column_id
    else:
        new_column_id = None

    print('Done.', 'NEW Column ID: ', new_column_id)
    logging.info('Done - New Column ID:' + str(new_column_id))

    return new_column_id


def Get_Columns(table_id):
    logging.info('Get_Columns SQL:' + str(dcfg.SQL_SELECT_COLUMNS.replace('{table_id}', str(table_id))))
    return _execute_sql(dcfg.SQL_SELECT_COLUMNS.replace('{table_id}', str(table_id)))


def Delete_Column(column_id):
    delete_sql = dcfg.SQL_DELETE_COLUMN.replace('{id}', str(column_id))
    logging.info('Delete_Column SQL:' + str(delete_sql))
    return _execute_sql_no_results(delete_sql)


def Delete_Table_Columns(table_id):
    delete_sql = dcfg.SQL_DELETE_TABLE_COLUMNS.replace('{table_id}', str(table_id))
    logging.info('Delete_Table_Columns SQL:' + str(delete_sql))
    return _execute_sql_no_results(delete_sql)


def Delete_Table(table_id):
    delete_sql = dcfg.SQL_DELETE_TABLE.replace('{table_id}', str(table_id))
    logging.info('Delete_Table SQL:' + str(delete_sql))
    try:
        result = _execute_sql_no_results(delete_sql)
    except Exception as e:
        print(str(e))
        logging.error(str(e))
        result = False
    return result

def Get_Max_Table_ID():
    return _execute_sql(dcfg.SQL_MAX_TABLE_ID)


def Get_Max_Column_ID():
    return _execute_sql(dcfg.SQL_MAX_COLUMN_ID)


def fetch_user_agents(user_id, user_role=None):
    try:
        conn = pyodbc.connect(
                f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
            )
        
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        # Query to fetch agents that the user has access to based on group assignments
        # Admins (role 3) see all agents
        if user_role >= 3:
            query = """
                SELECT DISTINCT a.id, a.description, a.objective
                FROM Agents a
                WHERE a.enabled = 1
            """
            cursor.execute(query)
        else:
            query = """
                SELECT DISTINCT a.id, a.description, a.objective
                FROM Agents a
                JOIN AgentGroups ag ON a.id = ag.agent_id
                JOIN UserGroups ug ON ag.group_id = ug.group_id
                WHERE ug.user_id = ? AND a.enabled = 1
            """
            cursor.execute(query, user_id)

        agents = cursor.fetchall()
        
        agent_list = [{'id': agent[0], 'description': agent[1], 'objective': agent[2]} for agent in agents]
    except Exception as e:
        print(str(e))
        return None
    return agent_list


def fetch_user_agents_by_email(user_email):
    try:
        conn = pyodbc.connect(
                f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
            )
        
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        # Query to fetch agents that the user has access to based on group assignments
        query = """
            SELECT a.*
            FROM Agents a
            INNER JOIN AgentGroups ag ON a.id = ag.agent_id
            INNER JOIN UserGroups ug ON ag.group_id = ug.group_id
            INNER JOIN [User] u ON ug.user_id = u.id
            WHERE u.email = ?
            """
        
        cursor.execute(query, user_email)
        agents = cursor.fetchall()
        
        agent_list = [{'id': agent[0], 'description': agent[1], 'objective': agent[2]} for agent in agents]
    except Exception as e:
        print(str(e))
        return []
    return agent_list


def execute_sql_query_as_df(query):
    try:
        conn = pyodbc.connect(
                f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
            )
        conn.cursor().execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        # Execute the SQL query and fetch the results into a Pandas DataFrame
        result_df = pd.read_sql_query(query, conn)

        return result_df
    except Exception as e:
        print("Error:", str(e))
        return None


def get_table_descriptions(connection_id):
    df = execute_sql_query_as_df(dcfg.SYS_SQL_SELECT_TABLE_DESCRIPTIONS.replace('{connection_id}', str(connection_id)))

    if df is None or df.empty:
        return 'TABLE | DESCRIPTION\n'

    final_string = 'TABLE | DESCRIPTION' + '\n'
    for index, row in df.iterrows():
        final_string += row['table_name'] + ' | ' + row['table_description'] + '\n'

    return final_string


def get_table_descriptions_as_yaml(connection_id):
    print('Get table descriptions from yaml:')
    print(dcfg.SYS_SQL_SELECT_TABLE_DESCRIPTIONS.replace('{connection_id}', str(connection_id)))
    df = execute_sql_query_as_df(dcfg.SYS_SQL_SELECT_TABLE_DESCRIPTIONS.replace('{connection_id}', str(connection_id)))

    if df is None or df.empty:
        return yaml.dump({'tables': {}}, sort_keys=False)

    print('RESULTS:')
    print(df.head())

    # Convert DataFrame to a dictionary with table name as key and description as value
    tables = {row['table_name']: row['table_description'] for _, row in df.iterrows()}

    # Format the final YAML
    yaml_str = yaml.dump({'tables': tables}, sort_keys=False)

    return yaml_str


def get_column_descriptions(table_list, connection_id):
    converted_table_list = ''

    if 'str' in str(type(table_list)):
        for index, table in enumerate(ast.literal_eval(table_list)):
            if index == 0:
                converted_table_list += "'" + table + "'"
            else:
                converted_table_list += ", '" + table + "'"
    else:
        for index, table in enumerate(table_list):
            if index == 0:
                converted_table_list += "'" + table + "'"
            else:
                converted_table_list += ", '" + table + "'"

    df = execute_sql_query_as_df(dcfg.SYS_SQL_SELECT_COLUMN_DESCRIPTIONS.replace('{connection_id}', str(connection_id)).replace('{table_list}', converted_table_list))

    if df is None or df.empty:
        return 'TABLE NAME | COLUMN NAME | COLUMN DESCRIPTION | COLUMN VALUES\n'

    final_string = 'TABLE NAME | COLUMN NAME | COLUMN DESCRIPTION | COLUMN VALUES' + '\n'
    for index, row in df.iterrows():
        final_string += row['table_name'] + ' | ' + row['column_name'] + ' | ' + row['column_description'] + ' | ' + row['column_values'] + '\n'

    return final_string


def get_all_column_descriptions_as_yaml(connection_id):
    df = execute_sql_query_as_df(dcfg.SYS_SQL_SELECT_COLUMN_DESCRIPTIONS_ALL.replace('{connection_id}', str(connection_id)))

    if df is None or df.empty:
        return yaml.dump({'tables': [{}]}, sort_keys=False)

    # Group by table name and construct the nested dictionary
    tables = {}
    for table, group in df.groupby('table_name'):
        columns = {row['column_name']: {'description': row['column_description'], 
                                       'values': row['column_values']}
                   for _, row in group.iterrows()}
        tables[table] = {'columns': columns}

    # Format the final YAML
    yaml_str = yaml.dump({'tables': [tables]}, sort_keys=False)

    return yaml_str


def get_column_descriptions_as_yaml(table_list, connection_id):
    converted_table_list = ''

    if 'str' in str(type(table_list)):
        for index, table in enumerate(ast.literal_eval(table_list)):
            if index == 0:
                converted_table_list += "'" + table + "'"
            else:
                converted_table_list += ", '" + table + "'"
    else:
        for index, table in enumerate(table_list):
            if index == 0:
                converted_table_list += "'" + table + "'"
            else:
                converted_table_list += ", '" + table + "'"

    df = execute_sql_query_as_df(dcfg.SYS_SQL_SELECT_COLUMN_DESCRIPTIONS.replace('{connection_id}', str(connection_id)).replace('{table_list}', converted_table_list))

    if df is None or df.empty:
        return yaml.dump({'tables': [{}]}, sort_keys=False)

    # Group by table name and construct the nested dictionary
    tables = {}
    for table, group in df.groupby('table_name'):
        columns = {row['column_name']: {'description': row['column_description'],
                                       'values': row['column_values']}
                   for _, row in group.iterrows()}
        tables[table] = {'columns': columns}

    # Format the final YAML
    yaml_str = yaml.dump({'tables': [tables]}, sort_keys=False)

    return yaml_str


def get_column_descriptions_with_table_descriptions_as_yaml(table_list, connection_id):
    converted_table_list = ''

    if 'str' in str(type(table_list)):
        for index, table in enumerate(ast.literal_eval(table_list)):
            if index == 0:
                converted_table_list += "'" + table + "'"
            else:
                converted_table_list += ", '" + table + "'"
    else:
        for index, table in enumerate(table_list):
            if index == 0:
                converted_table_list += "'" + table + "'"
            else:
                converted_table_list += ", '" + table + "'"

    df = execute_sql_query_as_df(
        dcfg.SYS_SQL_SELECT_COLUMN_WITH_TABLE_DESCRIPTIONS
        .replace('{connection_id}', str(connection_id))
        .replace('{table_list}', converted_table_list)
    )

    if df is None or df.empty:
        return yaml.dump({'tables': []}, sort_keys=False)

    # Group by table name and construct the nested dictionary
    tables = []
    for table, group in df.groupby('table_name'):
        # Get the table description (assumes it's the same for all rows in the group)
        table_description = group['table_description'].iloc[0]

        # Build the columns dictionary
        columns = {
            row['column_name']: {
                'description': row['column_description'],
                'values': row['column_values']
            }
            for _, row in group.iterrows()
        }

        # Append to tables list
        tables.append({
            'table_name': table,
            'table_description': table_description,
            'columns': columns
        })

    # Format the final YAML
    yaml_str = yaml.dump({'tables': tables}, sort_keys=False)

    return yaml_str


def script_create_table_statements(table_names, server_creds=None):
    """
    Scripts CREATE TABLE statements for the given tables from a SQL Server database.

    Parameters:
    server (str): The server name or IP.
    database (str): The database name.
    username (str): The username for authentication.
    password (str): The password for authentication.
    table_names (list): A list of table names to script.

    Returns:
    dict: A dictionary with table names as keys and their CREATE TABLE statements as values.
    """

    if server_creds is None:
        # Create a cursor object to interact with the default AI database
        conn_data = pyodbc.connect(
                f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
            )
        conn_temp = conn_data
    else:
        # Create a new server connection
        # Establish a connection to SQL Server
        conn_temp = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={server_creds['DB_SERVER']};DATABASE={server_creds['DB_NAME']};UID={server_creds['DB_USER']};PWD={server_creds['DB_PWD']}"
        )
    
    # Convert string to list of necessary
    if 'str' in str(type(table_names)):
        table_names = ast.literal_eval(table_names)

    # Connect to the SQL Server
    cursor = conn_temp.cursor()

    # Dictionary to store CREATE TABLE statements
    create_table_statements = {}

    for table in table_names:
        # Retrieve column details
        cursor.execute(f"SELECT * FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = N'{table}'")
        columns = cursor.fetchall()

        # Construct the CREATE TABLE statement
        create_statement = f"CREATE TABLE {table} (\n"
        column_definitions = []
        for column in columns:
            column_name = column.COLUMN_NAME
            data_type = column.DATA_TYPE
            nullable = "NULL" if column.IS_NULLABLE == "YES" else "NOT NULL"
            column_def = f"    {column_name} {data_type} {nullable}"
            column_definitions.append(column_def)
        create_statement += ",\n".join(column_definitions)
        create_statement += "\n);"

        create_table_statements[table] = create_statement

        return create_table_statements


def get_connection_string_DEPRECATED(agent_id):
    # Define the connection to the database
    conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        )
    
    cursor = conn.cursor()
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    # SQL query to select connection data based on agent_id
    query = '''
    SELECT c.server, c.database_name, c.user_name, c.password, c.connection_string
    FROM dbo.Connections c
    INNER JOIN dbo.AgentConnections ac ON c.id = ac.connection_id
    WHERE ac.agent_id = ?
    '''

    cursor.execute(query, agent_id)
    rows = cursor.fetchall()

    connections = []
    print(rows)
    # Construct or use the connection string
    for row in rows:
        print(row)
        temp_server, temp_database_name, temp_user_name, temp_password, connection_string = row

        if connection_string:
            connections.append(connection_string)
        else:
            constructed_connection_string = (
                f"Driver={{SQL Server}};"
                f"Server={temp_server};"
                f"Database={temp_database_name};"
                f"UID={temp_user_name};"
                f"PWD={temp_password};"
            )
            connections.append(constructed_connection_string)

    cursor.close()
    conn.close()

    return connections[0]


def get_connection_string_by_name(connection_name):
    # Define the connection to the database (assuming SQL Server for the main database)
    main_conn = pyodbc.connect(
        f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
    )

    cursor = main_conn.cursor()
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    # SQL query to select connection data based on agent_id
    query = '''
    SELECT c.server, c.database_name, c.user_name, c.password, c.connection_string, c.database_type, c.port, c.id connection_id
    FROM dbo.Connections c
    WHERE c.connection_name = ?
    '''

    cursor.execute(query, connection_name)
    rows = cursor.fetchall()

    connections = []
    connection_id = None
    database_type = None

    # Construct or use the connection string
    for row in rows:
        temp_server, temp_database_name, temp_user_name, temp_password, connection_string, temp_database_type, port, temp_connection_id = row
        
        connection_id = temp_connection_id
        database_type = temp_database_type

        if connection_string:
            connections.append(connection_string)
        else:
            if database_type == "SQL Server":
                constructed_connection_string = (
                    f"Driver={{SQL Server}};"
                    f"Server={temp_server},{port};"
                    f"Database={temp_database_name};"
                    f"UID={temp_user_name};"
                    f"PWD={temp_password};"
                )
            elif database_type == "Oracle":
                constructed_connection_string = (
                    f"Driver={{Oracle}};"
                    f"User Id={temp_user_name};"
                    f"Password={temp_password};"
                    f"Data Source={temp_server}:{port}/{temp_database_name};"
                )
            elif database_type == "Postgres":
                constructed_connection_string = (
                    f"Driver={{PostgreSQL Unicode}};"
                    f"Server={temp_server};"
                    f"Port={port};"
                    f"Database={temp_database_name};"
                    f"UID={temp_user_name};"
                    f"PWD={temp_password};"
                )
            elif database_type == "Snowflake":
                constructed_connection_string = (
                    f"Driver={{SnowflakeDSIIDriver}};"
                    f"Server={temp_server};"
                    f"Port={port};"
                    f"Database={temp_database_name};"
                    f"UID={temp_user_name};"
                    f"PWD={temp_password};"
                )
            else:
                constructed_connection_string = (
                    f"Driver={{ODBC Driver}};"
                    f"Server={temp_server};"
                    f"Port={port};"
                    f"Database={temp_database_name};"
                    f"UID={temp_user_name};"
                    f"PWD={temp_password};"
                )

            connections.append(constructed_connection_string)

    cursor.close()
    main_conn.close()

    return connections[0] if connections else None


import re

def replace_connection_placeholders(text):
    """
    Finds all {CONN:connection_name} placeholders in a string and replaces them
    with the actual connection strings.
    
    Args:
        text (str): The string containing connection placeholders
    
    Returns:
        str: Text with connection placeholders replaced with actual connection strings
    """
    # Regular expression to match the pattern {CONN:connection_name}
    pattern = r'\{CONN:([^}]+)\}'
    
    def replace_match(match):
        connection_name = match.group(1).strip()
        # Get the actual connection string using the helper function
        actual_connection = get_connection_string_by_name(connection_name)
        return actual_connection
    
    # Replace all occurrences
    processed_text = re.sub(pattern, replace_match, text)
    
    return processed_text


def get_connection_string(agent_id):
    # Define the connection to the database (assuming SQL Server for the main database)
    main_conn = pyodbc.connect(
        f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
    )

    cursor = main_conn.cursor()
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    # SQL query to select connection data based on agent_id
    query = '''
    SELECT c.server, c.database_name, c.user_name, c.password, c.connection_string, c.database_type, c.port, c.id connection_id, c.odbc_driver, c.[parameters]
    FROM dbo.Connections c
    INNER JOIN dbo.AgentConnections ac ON c.id = ac.connection_id
    WHERE ac.agent_id = ?
    '''

    cursor.execute(query, agent_id)
    rows = cursor.fetchall()

    connections = []
    connection_id = None
    database_type = None

    from connection_secrets import resolve_connection_string_secrets

    # Construct or use the connection string
    for row in rows:
        temp_server, temp_database_name, temp_user_name, temp_password, connection_string, temp_database_type, port, temp_connection_id, temp_odbc_driver, temp_parameters = row
        
        connection_id = temp_connection_id
        database_type = temp_database_type

        if connection_string:
            # Resolve references 
            connection_string = resolve_connection_string_secrets(connection_string)
            connections.append(connection_string)
        else:
            constructed_connection_string = generate_connection_string(database_type, temp_server, port, temp_database_name, temp_user_name, temp_password, temp_parameters, temp_odbc_driver)
            # Resolve references 
            constructed_connection_string = resolve_connection_string_secrets(constructed_connection_string)
            connections.append(constructed_connection_string)

    cursor.close()
    main_conn.close()

    return connections[0] if connections else None, connection_id, database_type


def get_database_connection_string(connection_id):
    """Get connection string for a database connection"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get connection details
        cursor.execute("""
            SELECT connection_string, id, database_type, odbc_driver, 
                   server, port, database_name, user_name, password, parameters
            FROM Connections 
            WHERE id = ?
        """, connection_id)
        
        row = cursor.fetchone()
        if not row:
            return None, None, None
        
        conn_str = row[0]
        conn_id = row[1]
        db_type = row[2]
        odbc_driver = row[3]
        server = row[4]
        port = row[5]
        db_name = row[6]
        user = row[7]
        password = row[8]
        parameters = row[9]
        
        # Resolve LOCAL_SECRET references in password field
        if password and isinstance(password, str) and '{{LOCAL_SECRET:' in password:
            try:
                from local_secrets import get_local_secret
                import re
                match = re.search(r'\{\{LOCAL_SECRET:(.+?)\}\}', password)
                if match:
                    secret_key = match.group(1)
                    resolved = get_local_secret(secret_key, None)
                    if resolved:
                        password = resolved
                        logging.info(f"[get_db_conn_str] Resolved LOCAL_SECRET for connection {connection_id}")
            except Exception as e:
                logging.warning(f"[get_db_conn_str] Could not resolve LOCAL_SECRET: {e}")
        
        # If connection string is empty or not provided, generate it
        logging.info(f"[get_db_conn_str] conn_id={connection_id} stored_conn_str='{conn_str}' server='{server}' port={port} db='{db_name}' driver='{odbc_driver}'")
        if not conn_str or conn_str.strip() == '' or conn_str.strip() == 'None':
            conn_str = generate_connection_string(
                db_type, server, port, db_name, user, password, parameters, odbc_driver
            )
            logging.info(f"[get_db_conn_str] Generated conn_str: '{conn_str}'") if conn_str else None
        
        # Safety net: resolve any remaining secret references in connection string
        try:
            from connection_secrets import resolve_connection_string_secrets
            conn_str = resolve_connection_string_secrets(conn_str)
        except Exception:
            pass

        cursor.close()
        conn.close()

        return conn_str, conn_id, db_type
    except Exception as e:
        logging.error(f"Error getting database connection string: {str(e)}")
        return None, None, None


def get_database_connection_string_legacy(connection_id):
    # Define the connection to the database (assuming SQL Server for the main database)
    main_conn = pyodbc.connect(
        f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
    )

    cursor = main_conn.cursor()
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    # SQL query to select connection data based on agent_id
    query = '''
    SELECT c.server, c.database_name, c.user_name, c.password, c.connection_string, c.database_type, c.port, c.id connection_id
    FROM dbo.Connections c
    WHERE c.id = ?
    '''

    cursor.execute(query, connection_id)
    rows = cursor.fetchall()

    connections = []
    connection_id = None
    database_type = None

    # Construct or use the connection string
    for row in rows:
        temp_server, temp_database_name, temp_user_name, temp_password, connection_string, temp_database_type, port, temp_connection_id = row
        
        connection_id = temp_connection_id
        database_type = temp_database_type

        if connection_string:
            connections.append(connection_string)
        else:
            if database_type == "SQL Server":
                constructed_connection_string = (
                    f"Driver={{SQL Server}};"
                    f"Server={temp_server},{port};"
                    f"Database={temp_database_name};"
                    f"UID={temp_user_name};"
                    f"PWD={temp_password};"
                )
            elif database_type == "Oracle":
                constructed_connection_string = (
                    f"Driver={{Oracle}};"
                    f"User Id={temp_user_name};"
                    f"Password={temp_password};"
                    f"Data Source={temp_server}:{port}/{temp_database_name};"
                )
            elif database_type == "Postgres":
                constructed_connection_string = (
                    f"Driver={{PostgreSQL Unicode}};"
                    f"Server={temp_server};"
                    f"Port={port};"
                    f"Database={temp_database_name};"
                    f"UID={temp_user_name};"
                    f"PWD={temp_password};"
                )
            elif database_type == "Snowflake":
                constructed_connection_string = (
                    f"Driver={{SnowflakeDSIIDriver}};"
                    f"Server={temp_server};"
                    f"Port={port};"
                    f"Database={temp_database_name};"
                    f"UID={temp_user_name};"
                    f"PWD={temp_password};"
                )
            else:
                constructed_connection_string = (
                    f"Driver={{ODBC Driver}};"
                    f"Server={temp_server};"
                    f"Port={port};"
                    f"Database={temp_database_name};"
                    f"UID={temp_user_name};"
                    f"PWD={temp_password};"
                )

            connections.append(constructed_connection_string)

    cursor.close()
    main_conn.close()

    return connections[0] if connections else None, connection_id, database_type


def execute_sql_no_results(connection_string, sql_query):
    try:
        #logging.debug('Function: _execute_sql_no_results')
        #logging.debug('SQL Statement: ' + str(sql_query))
        # Establish a connection to SQL Server
        # conn = pyodbc.connect(
        #     f"DRIVER={{SQL Server}};SERVER={database_server};DATABASE={database_name};UID={username};PWD={password}"
        # )
        conn = pyodbc.connect(connection_string)

        # Create a cursor object to interact with the database
        cursor = conn.cursor()
        
        # Execute the SQL query
        cursor.execute(sql_query)

        conn.commit()

        cursor.close()
        
        return True
    except Exception as e:
        print(f"Error: {str(e)}")
        return False
    

def execute_sql_for_llm(connection_string, sql_query):
    try:
        conn = pyodbc.connect(connection_string)
        print('Connected.')
        cursor = conn.cursor()
        print('Running query:', sql_query)
        cursor.execute(sql_query)
        if cursor.description is not None:
            print('Cursor Desc:', cursor.description)
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchall()
            print('Rows:', rows)
            result = {
                'status': 'success',
                "columns": columns,
                "rows": [[str(value) for value in row] for row in rows]
            }
        else:
            print('WARNING: No data returned')
            result = {
                'status': 'success',
                "columns": "",
                "rows": ""
            }
        print('Result:', result)
        cursor.close()
        conn.commit()
        
        return json.dumps(result, indent=2)
    except Exception as e:
        result = {
                'status': 'error',
                "error": str(e),
                "columns": "",
                "rows": ""
            }
        return json.dumps(result, indent=2)


def execute_sql_query(connection_string, sql_query):
    try:
        conn = pyodbc.connect(connection_string)
        print('Connected.')
        cursor = conn.cursor()
        print('Running query:', sql_query)
        cursor.execute(sql_query)
        if cursor.description is not None:
            print('Cursor Desc:', cursor.description)
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchall()
            print('Rows:', rows)
            result = {
                'status': 'success',
                "columns": columns,
                "rows": [[str(value) for value in row] for row in rows]
            }
        else:
            print('WARNING: No data returned')
            result = {
                'status': 'success',
                "columns": "",
                "rows": ""
            }
        print('Result:', result)
        cursor.close()
        conn.commit()
        
        return result
    except Exception as e:
        result = {
                'status': 'error',
                "error": str(e),
                "columns": "",
                "rows": ""
            }
        return result
    

"""
Agent Environment Helper Functions
Utility functions to detect and manage agent-environment associations
"""

def get_agent_environment(agent_id: int) -> Optional[str]:
    """
    Simple function to get an agent's assigned environment ID.
    
    Args:
        agent_id: The agent's ID
        
    Returns:
        Environment ID if assigned, None otherwise
    """
    import os
    
    # Get connection details from environment
    connection_string = get_db_connection_string()
    if not connection_string:
        try:
            import config as cfg
            connection_string = (
                f"DRIVER={{SQL Server}};"
                f"SERVER={cfg.DATABASE_SERVER};"
                f"DATABASE={cfg.DATABASE_NAME};"
                f"UID={cfg.DATABASE_UID};"
                f"PWD={cfg.DATABASE_PWD}"
            )
        except ImportError:
            print("Could not build connection string")
            return None
    
    tenant_id = os.getenv('API_KEY')
    if not tenant_id:
        print("No tenant ID available")
        return None
    
    try:
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", tenant_id)
        
        # Check for active environment assignment
        cursor.execute("""
            SELECT TOP 1 aea.environment_id
            FROM AgentEnvironmentAssignments aea
            INNER JOIN AgentEnvironments ae 
                ON aea.environment_id = ae.environment_id
            WHERE aea.agent_id = ? 
                AND aea.is_active = 1
                AND ae.is_deleted = 0
                AND ae.status = 'active'
            ORDER BY aea.assigned_date DESC
        """, agent_id)
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            print(f"Agent {agent_id} has environment: {row.environment_id}")
            return row.environment_id
        else:
            print(f"Agent {agent_id} has no active environment")
            return None
            
    except Exception as e:
        print(f"Error checking agent environment: {e}")
        return None


def should_use_environment(agent_id: int,
                          environment_id: Optional[str] = None,
                          use_environment: Optional[bool] = None,
                          auto_detect: bool = True) -> Tuple[bool, Optional[str]]:
    """
    Determine if an agent should use an environment.
    
    Priority order:
    1. Explicit environment_id provided -> use it
    2. use_environment=False -> don't use any
    3. use_environment=True -> auto-detect agent's environment
    4. auto_detect=True -> check if agent has environment
    5. Default -> no environment
    
    Args:
        agent_id: The agent's ID
        environment_id: Explicitly specified environment ID
        use_environment: Explicit flag to use/not use environment
        auto_detect: Whether to auto-detect (default True)
        
    Returns:
        Tuple of (should_use: bool, environment_id: str or None)
    """
    
    # Priority 1: Explicit environment ID
    if environment_id:
        print(f"Using explicit environment: {environment_id}")
        return True, environment_id
    
    # Priority 2: Explicitly disabled
    if use_environment is False:
        print("Environment explicitly disabled")
        return False, None
    
    # Priority 3: Explicitly enabled (need to find which one)
    if use_environment is True:
        env_id = get_agent_environment(agent_id)
        if env_id:
            print(f"Found environment for agent: {env_id}")
            return True, env_id
        else:
            print(f"use_environment=True but agent {agent_id} has no environment")
            return False, None
    
    # Priority 4: Auto-detect if enabled
    if auto_detect:
        env_id = get_agent_environment(agent_id)
        if env_id:
            print(f"Auto-detected environment {env_id} for agent {agent_id}")
            return True, env_id
    
    # Default: No environment
    return False, None


def get_connection_types_with_icons():
    """
    Return all available connection types with their icon configurations
    Checks for actual icon files in static/icons folder
    """
    
    # Get the path to the static/icons folder
    icons_folder = os.path.join('static', 'icons')
    
    # Get list of available icon files (store full filenames)
    available_icon_files = {}
    if os.path.exists(icons_folder):
        for file in os.listdir(icons_folder):
            if file.lower().endswith(('.png', '.svg', '.jpg', '.jpeg', '.ico')):
                # Store with lowercase key for case-insensitive matching
                name_without_ext = os.path.splitext(file)[0].lower()
                available_icon_files[name_without_ext] = file  # Store actual filename
    
    connection_types = {
        # Traditional Databases
        "sql_server": {
            "name": "SQL Server",
            "category": "database",
            "icon_class": "fa-database",
            "icon_color": "#CC2927",
            "defaultDriver": "ODBC Driver 17 for SQL Server",
            "defaultPort": "1433",
            "fields": "standard_db"
        },
        "oracle": {
            "name": "Oracle",
            "category": "database",
            "icon_class": "fa-database",
            "icon_color": "#F80000",
            "defaultDriver": "Oracle in OraClient12Home1",
            "defaultPort": "1521",
            "fields": "standard_db"
        },
        "postgresql": {
            "name": "PostgreSQL",
            "category": "database",
            "icon_class": "fa-database",
            "icon_color": "#336791",
            "defaultDriver": "PostgreSQL UNICODE",
            "defaultPort": "5432",
            "fields": "standard_db"
        },
        "mysql": {
            "name": "MySQL",
            "category": "database",
            "icon_class": "fa-database",
            "icon_color": "#4479A1",
            "defaultDriver": "MySQL ODBC 8.0 Driver",
            "defaultPort": "3306",
            "fields": "standard_db"
        },
        "snowflake": {
            "name": "Snowflake",
            "category": "database",
            "icon_class": "fa-snowflake",
            "icon_color": "#29B5E8",
            "defaultDriver": "SnowflakeDSIIDriver",
            "defaultPort": "443",
            "fields": "standard_db"
        },
        
        # ERP Systems
        "netsuite": {
            "name": "Oracle NetSuite",
            "category": "erp",
            "iconText": "NS",
            "icon_color": "#1C74BB",
            "defaultDriver": "CData ODBC Driver for REST",
            "fields": "netsuite",
            "requires_oauth": True
        },
        "sap": {
            "name": "SAP",
            "category": "erp",
            "iconText": "SAP",
            "icon_color": "#0FAAFF",
            "defaultDriver": "CData ODBC Driver for SAP",
            "fields": "sap"
        },
        "dynamics365": {
            "name": "Microsoft Dynamics 365",
            "category": "erp",
            "iconText": "D365",
            "icon_color": "#0078D4",
            "defaultDriver": "CData ODBC Driver for REST",
            "fields": "dynamics"
        },
        
        # CRM Systems
        "salesforce": {
            "name": "Salesforce",
            "category": "crm",
            "iconText": "SF",
            "icon_color": "#00A1E0",
            "defaultDriver": "CData ODBC Driver for Salesforce",
            "fields": "salesforce",
            "requires_oauth": False
        },
        "hubspot": {
            "name": "HubSpot",
            "category": "crm",
            "iconText": "HS",
            "icon_color": "#FF7A59",
            "defaultDriver": "CData ODBC Driver for REST",
            "fields": "hubspot"
        },
        
        # Cloud Storage
        "sharepoint": {
            "name": "SharePoint",
            "category": "cloud",
            "iconText": "SP",
            "icon_color": "#0078D4",
            "defaultDriver": "CData ODBC Driver for SharePoint",
            "fields": "sharepoint"
        },
        "google_sheets": {
            "name": "Google Sheets",
            "category": "cloud",
            "icon_class": "fa-file-spreadsheet",
            "icon_color": "#0F9D58",
            "defaultDriver": "CData ODBC Driver for Google Sheets",
            "fields": "google_sheets"
        },
        "s3": {
            "name": "Amazon S3",
            "category": "cloud",
            "iconText": "S3",
            "icon_color": "#FF9900",
            "defaultDriver": "CData ODBC Driver for Amazon S3",
            "fields": "s3"
        },
        
        # APIs and Files
        "rest_api": {
            "name": "Generic REST API",
            "category": "api",
            "icon_class": "fa-plug",
            "icon_color": "#6C757D",
            "defaultDriver": "CData ODBC Driver for REST",
            "fields": "rest_api"
        },
        "excel": {
            "name": "Excel Files",
            "category": "file",
            "icon_class": "fa-file-excel",
            "icon_color": "#1D6F42",
            "defaultDriver": "Microsoft Excel Driver (*.xls, *.xlsx, *.xlsm, *.xlsb)",
            "fields": "excel"
        },
        "csv": {
            "name": "CSV Files",
            "category": "file",
            "icon_class": "fa-file-csv",
            "icon_color": "#6C757D",
            "defaultDriver": "Microsoft Access Text Driver (*.txt, *.csv)",
            "fields": "csv"
        }
    }
    
    # Check for actual icon files and update the connection types
    for key, conn_type in connection_types.items():
        # Check for icon file - try exact key match first
        if key.lower() in available_icon_files:
            conn_type['icon_type'] = 'image'
            conn_type['icon_path'] = available_icon_files[key.lower()]  # Use actual filename
            continue
            
        # Try alternative names
        possible_names = [
            key.replace('_', ''),  # remove underscores
            key.replace('_', '-'),  # replace underscores with hyphens
            conn_type['name'].lower().replace(' ', ''),  # use name without spaces
            conn_type['name'].lower().replace(' ', '_'),  # use name with underscores
            conn_type['name'].lower().replace(' ', '-'),  # use name with hyphens
        ]
        
        icon_found = False
        for name in possible_names:
            if name in available_icon_files:
                conn_type['icon_type'] = 'image'
                conn_type['icon_path'] = available_icon_files[name]  # Use actual filename
                icon_found = True
                break
    
    # Check which CData drivers are actually installed
    try:
        import pyodbc
        installed_drivers = pyodbc.drivers()

        # Create a lowercase version of installed drivers for comparison
        installed_drivers_lower = [driver.lower() for driver in installed_drivers]
        
        for key, conn_type in connection_types.items():
            default_driver = conn_type.get('defaultDriver', '')
            conn_type['driver_available'] = default_driver.lower() in installed_drivers_lower
    except:
        for key, conn_type in connection_types.items():
            conn_type['driver_available'] = True
    
    return connection_types


def query_app_database(query: str, params: tuple = None):
    """
    Query the application's metadata database with proper tenant context.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context - CRITICAL for RLS
        api_key = os.getenv('API_KEY')
        cursor.execute("EXEC tenant.sp_setTenantContext ?", api_key)
        
        # Execute query
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        # Fetch results if SELECT
        if query.strip().upper().startswith('SELECT'):
            columns = [column[0] for column in cursor.description]
            results = []
            for row in cursor.fetchall():
                results.append(dict(zip(columns, row)))
            return results
        else:
            # For INSERT/UPDATE/DELETE
            conn.commit()
            return []
            
    except Exception as e:
        print(f"Error querying app database: {str(e)}")
        raise
    finally:
        cursor.close()
        conn.close()


# =====================================================
# ENHANCED METADATA FUNCTIONS
# =====================================================

def get_enhanced_table_metadata_as_yaml(connection_id):
    """
    Retrieve enhanced table metadata with business context.
    
    Returns YAML with:
    - Table descriptions
    - Table types (fact/dimension/etc)
    - Business rules
    - Common filters
    - Related tables and join patterns
    
    Falls back to empty string if no enhanced metadata exists.
    """
    try:
        query = """
            SELECT 
                table_name,
                table_description,
                table_type,
                table_category,
                primary_key_columns,
                refresh_frequency,
                business_rules,
                common_filters,
                related_tables
            FROM llm_Tables
            WHERE connection_id = ?
            ORDER BY table_name
        """
        
        tables = query_app_database(query, (connection_id,))
        
        if not tables:
            return ""
        
        # Build YAML structure
        yaml_output = "# ENHANCED TABLE METADATA\n\n"
        yaml_output += "tables:\n"
        
        for table in tables:
            yaml_output += f"  - name: {table['table_name']}\n"
            
            if table['table_description']:
                yaml_output += f"    description: \"{table['table_description']}\"\n"
            
            if table['table_type']:
                yaml_output += f"    type: {table['table_type']}\n"
            
            if table['table_category']:
                yaml_output += f"    category: {table['table_category']}\n"
            
            if table['primary_key_columns']:
                yaml_output += f"    primary_keys: {table['primary_key_columns']}\n"
            
            if table['refresh_frequency']:
                yaml_output += f"    refresh_frequency: {table['refresh_frequency']}\n"
            
            # Business rules (JSON field)
            if table['business_rules']:
                try:
                    rules = json.loads(table['business_rules'])
                    if rules.get('rules'):
                        yaml_output += "    business_rules:\n"
                        for rule in rules['rules']:
                            yaml_output += f"      - \"{rule}\"\n"
                    
                    if rules.get('defaults'):
                        yaml_output += "    defaults:\n"
                        for key, value in rules['defaults'].items():
                            yaml_output += f"      {key}: \"{value}\"\n"
                except:
                    pass
            
            # Common filters (JSON field)
            if table['common_filters']:
                try:
                    filters = json.loads(table['common_filters'])
                    if filters:
                        yaml_output += "    common_filters:\n"
                        
                        if filters.get('recommended'):
                            yaml_output += "      recommended:\n"
                            for filt in filters['recommended']:
                                yaml_output += f"        - \"{filt}\"\n"
                        
                        if filters.get('required'):
                            yaml_output += "      required:\n"
                            for filt in filters['required']:
                                yaml_output += f"        - \"{filt}\"\n"
                except:
                    pass
            
            # Related tables (JSON field)
            if table['related_tables']:
                try:
                    related = json.loads(table['related_tables'])
                    if related.get('commonly_joined_with'):
                        yaml_output += "    related_tables:\n"
                        for rel in related['commonly_joined_with']:
                            desc = f"{rel['table']} (frequency: {rel.get('frequency', 'unknown')}, join: {rel.get('join_type', 'INNER')})"
                            if rel.get('description'):
                                desc += f" - {rel['description']}"
                            yaml_output += f"      - \"{desc}\"\n"
                except:
                    pass
            
            yaml_output += "\n"
        
        return yaml_output
        
    except Exception as e:
        print(f"Error getting enhanced table metadata: {str(e)}")
        return ""


def get_enhanced_column_metadata_as_yaml(table_list, connection_id):
    """
    Retrieve enhanced column metadata with semantic info.
    
    Returns YAML with:
    - Column descriptions
    - Semantic types (email, phone, amount, etc)
    - Value formats and units
    - Common aggregations
    - Synonyms for natural language matching
    - Example values
    - Sensitivity flags
    
    Args:
        table_list: List of table names or single table name
        connection_id: Database connection ID
    """
    try:
        # Convert table_list to list if it's a string
        if isinstance(table_list, str):
            table_list = [table_list]
        
        if not table_list:
            return ""
        
        # Build parameterized query
        placeholders = ','.join(['?' for _ in table_list])
        
        query = f"""
            SELECT 
                t.table_name,
                c.column_name,
                c.column_description,
                c.data_type,
                c.data_type_precision,
                c.semantic_type,
                c.value_format,
                c.units,
                c.common_aggregations,
                c.synonyms,
                c.examples,
                c.is_sensitive,
                c.is_nullable,
                c.is_primary_key,
                c.is_foreign_key,
                c.foreign_key_table,
                c.foreign_key_column,
                c.value_range
            FROM llm_Columns c
            INNER JOIN llm_Tables t ON c.table_id = t.id
            WHERE t.connection_id = ? 
            AND t.table_name IN ({placeholders})
            AND c.is_calculated = 0
            ORDER BY t.table_name, c.id
        """
        
        # Execute with connection_id + table names
        params = (connection_id,) + tuple(table_list)
        columns = query_app_database(query, params)
        
        if not columns:
            return ""
        
        # Group by table
        tables_dict = {}
        for col in columns:
            table_name = col['table_name']
            if table_name not in tables_dict:
                tables_dict[table_name] = []
            tables_dict[table_name].append(col)
        
        # Build YAML
        yaml_output = "# ENHANCED COLUMN METADATA\n\n"
        yaml_output += "tables:\n"
        
        for table_name, cols in tables_dict.items():
            yaml_output += f"  {table_name}:\n"
            yaml_output += "    columns:\n"
            
            for col in cols:
                yaml_output += f"      - name: {col['column_name']}\n"
                
                if col['column_description']:
                    yaml_output += f"        description: \"{col['column_description']}\"\n"
                
                yaml_output += f"        type: {col['data_type_precision'] or col['data_type']}\n"
                
                if col['semantic_type']:
                    yaml_output += f"        semantic_type: {col['semantic_type']}\n"
                
                if col['value_format']:
                    yaml_output += f"        format: {col['value_format']}\n"
                
                if col['units']:
                    yaml_output += f"        units: {col['units']}\n"
                
                if col['common_aggregations']:
                    yaml_output += f"        aggregations: {col['common_aggregations']}\n"
                
                if col['synonyms']:
                    yaml_output += f"        synonyms: \"{col['synonyms']}\"\n"
                
                if col['examples']:
                    yaml_output += f"        examples: \"{col['examples']}\"\n"
                
                if col['value_range']:
                    yaml_output += f"        value_range: \"{col['value_range']}\"\n"
                
                yaml_output += f"        sensitive: {bool(col['is_sensitive'])}\n"
                yaml_output += f"        nullable: {bool(col['is_nullable'])}\n"
                yaml_output += f"        primary_key: {bool(col['is_primary_key'])}\n"
                
                if col['is_foreign_key'] and col['foreign_key_table']:
                    yaml_output += f"        foreign_key: {col['foreign_key_table']}.{col['foreign_key_column']}\n"
                
                yaml_output += "\n"
        
        return yaml_output
        
    except Exception as e:
        print(f"Error getting enhanced column metadata: {str(e)}")
        return ""


def get_calculated_metrics_as_yaml(connection_id):
    """
    Retrieve virtual/calculated metrics that can be used in queries.
    
    Returns YAML with:
    - Metric names
    - Calculation formulas
    - Dependencies
    - Semantic types
    """
    try:
        query = """
            SELECT
                t.table_name,
                c.column_name as metric_name,
                c.column_description as description,
                c.calculation_formula,
                c.calculation_dependencies,
                c.semantic_type,
                c.value_format,
                c.units,
                c.synonyms
            FROM llm_Columns c
            INNER JOIN llm_Tables t ON c.table_id = t.id
            WHERE t.connection_id = ?
            AND c.is_calculated = 1
            ORDER BY t.table_name, c.column_name
        """

        metrics = query_app_database(query, (connection_id,))

        if not metrics:
            return ""

        yaml_output = "# CALCULATED METRICS\n\n"
        yaml_output += "metrics:\n"

        for metric in metrics:
            yaml_output += f"  - name: {metric['metric_name']}\n"
            yaml_output += f"    table: {metric['table_name']}\n"

            if metric['description']:
                yaml_output += f"    description: \"{metric['description']}\"\n"

            if metric['calculation_formula']:
                yaml_output += f"    formula: \"{metric['calculation_formula']}\"\n"

            if metric['calculation_dependencies']:
                try:
                    deps = json.loads(metric['calculation_dependencies'])
                    yaml_output += f"    dependencies: {deps}\n"
                except:
                    pass

            if metric['semantic_type']:
                yaml_output += f"    type: {metric['semantic_type']}\n"

            if metric['value_format']:
                yaml_output += f"    format: {metric['value_format']}\n"

            if metric['units']:
                yaml_output += f"    units: {metric['units']}\n"

            if metric['synonyms']:
                yaml_output += f"    synonyms: \"{metric['synonyms']}\"\n"

            yaml_output += "\n"
        
        return yaml_output
        
    except Exception as e:
        print(f"Error getting calculated metrics: {str(e)}")
        return ""


def get_enhanced_full_schema_as_yaml(connection_id):
    """
    Get complete enhanced schema with tables, columns, and relationships.
    This is the "kitchen sink" function that returns everything.
    
    Use this as a drop-in replacement for get_all_column_descriptions_as_yaml()
    when enhanced metadata is available.
    """
    try:
        output = "# ENHANCED DATABASE SCHEMA\n"
        output += "# This schema includes business rules, semantic types, and relationships\n\n"
        
        # Get enhanced table metadata
        table_metadata = get_enhanced_table_metadata_as_yaml(connection_id)
        if table_metadata:
            output += table_metadata + "\n"
        
        # Get all table names for this connection
        query = "SELECT table_name FROM llm_Tables WHERE connection_id = ? ORDER BY table_name"
        tables = query_app_database(query, (connection_id,))
        table_names = [t['table_name'] for t in tables]
        
        # Get enhanced column metadata for all tables
        if table_names:
            column_metadata = get_enhanced_column_metadata_as_yaml(table_names, connection_id)
            if column_metadata:
                output += column_metadata + "\n"
        
        # Get calculated metrics
        metrics = get_calculated_metrics_as_yaml(connection_id)
        if metrics:
            output += metrics + "\n"
        
        return output if output != "# ENHANCED DATABASE SCHEMA\n# This schema includes business rules, semantic types, and relationships\n\n" else ""
        
    except Exception as e:
        print(f"Error getting enhanced full schema: {str(e)}")
        return ""


# =====================================================
# BACKWARD COMPATIBILITY HELPER
# =====================================================

def get_schema_for_nlq(connection_id, use_enhanced=True):
    """
    Smart function that tries enhanced schema first, falls back to basic.
    Use this in your NLQ code for easy backward compatibility.
    
    Args:
        connection_id: Database connection ID
        use_enhanced: If True, try enhanced schema first (default)
    
    Returns:
        Schema as YAML string
    """
    if use_enhanced:
        try:
            enhanced = get_enhanced_full_schema_as_yaml(connection_id)
            if enhanced:
                print("✓ Using enhanced schema with AI-generated metadata")
                return enhanced
        except Exception as e:
            print(f"⚠ Enhanced schema not available: {str(e)}")
    
    # Fall back to basic schema (your existing function)
    try:
        print("Using basic schema")
        return get_all_column_descriptions_as_yaml(connection_id)
    except Exception as e:
        print(f"Error getting basic schema: {str(e)}")
        return ""


def get_enhanced_full_schema_with_column_details_as_yaml(table_list, connection_id):
    """
    Get comprehensive schema for specific tables including:
    - Table business rules and filters
    - Column details with semantic types
    - Calculated metrics
    
    This function is used during SQL generation to give AI full context.
    
    Args:
        table_list: List of table names to include
        connection_id: Database connection ID
        
    Returns:
        YAML string with complete metadata, or falls back to basic schema
    """
    try:
        output = "# DATABASE SCHEMA WITH ENHANCED METADATA\n\n"
        
        # Get basic column descriptions (your existing function - fallback)
        try:
            basic_schema = get_column_descriptions_with_table_descriptions_as_yaml(table_list, connection_id)
        except:
            basic_schema = ""
        
        # Get enhanced table metadata
        table_metadata = get_enhanced_table_metadata_as_yaml(connection_id)
        
        # Get enhanced column metadata
        column_metadata = get_enhanced_column_metadata_as_yaml(table_list, connection_id)
        
        # Get calculated metrics
        metrics = get_calculated_metrics_as_yaml(connection_id)
        
        # If we have enhanced metadata, use it
        if table_metadata or column_metadata or metrics:
            print("✓ Using enhanced schema with AI-generated metadata")
            
            output += "## BASIC SCHEMA\n"
            output += basic_schema + "\n\n"
            
            if table_metadata:
                output += "## TABLE CONTEXT (Business Rules & Filters)\n"
                output += table_metadata + "\n"
            
            if column_metadata:
                output += "## COLUMN CONTEXT (Semantic Types & Examples)\n"
                output += column_metadata + "\n"
            
            if metrics:
                # Note: get_calculated_metrics_as_yaml() already includes its own
                # "# CALCULATED METRICS" header, so we don't add another one here
                output += metrics + "\n"
            
            return output
        else:
            # No enhanced metadata, use basic schema only
            print("⚠ No enhanced metadata available, using basic schema")
            return basic_schema
            
    except Exception as e:
        print(f"Error getting enhanced schema: {str(e)}")
        # Fall back to basic schema
        try:
            return get_column_descriptions_with_table_descriptions_as_yaml(table_list, connection_id)
        except:
            return ""
        



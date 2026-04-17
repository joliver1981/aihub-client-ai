
DATASET_TUPLE = ("AUTO","BI","ERP","Warehouse")

DATASET_TABLES = {
    "AUTO": [],
    "BI": ["vw_JOB_STATUS_CURRENT_DAY"],
    "ERP": ["vw_JOB_STATUS_CURRENT_DAY"],
    "Warehouse": ["vw_JOB_STATUS_CURRENT_DAY"],
    }

DATASET_TABLE_DESCRIPTIONS = {
    "BI": "BI jobs.",
    "ERP": "ERP jobs.",
    "Warehouse": "WMS jobs.",
}


DATASET_TABLE_KEY_FIELDS = [
    {
        "table": "vw_JOB_STATUS_CURRENT_DAY",
        "fields": [
            {
                "name": "Job_ID",
                "values": [],
                "description": "Unique identifier of the SQL server job.",
            },
            {
                "name": "Status",
                "values": ["SUCCESS","FAIL"],
                "description": "Indicates the status of the job.",
            },
        ],
    },
]

SQL_INSERT_JOB_HEADER = """
INSERT INTO [dbo].[JobHeader]
           (
            [description]
           ,[ai_system]
           ,[ai_prompt]
           ,[enabled]
           )
VALUES (
        {job_desc}
        ,{ai_system}
        ,{ai_prompt}
        ,{enabled}
        )
"""

SQL_UPDATE_JOB_HEADER = """
UPDATE [dbo].[JobHeader]
            SET [description] = {job_desc}, 
           ,[ai_system] = {ai_system}, 
           ,[ai_prompt] = {ai_prompt}, 
           ,[enabled] = {enabled}
WHERE id = {job_id}
"""

SQL_MERGE_JOB_HEADER = """
MERGE [dbo].[JobHeader] AS tgt
USING (VALUES
           ({job_id}
            ,{job_desc}
           ,{ai_system}
           ,{ai_prompt}
           ,{enabled}
           ,{collection_id}
           ,{pass_fail}
           )) AS src (job_id, job_desc, ai_system, ai_prompt, enabled, collection_id, pass_fail)
           ON tgt.id = src.job_id
WHEN MATCHED THEN
    UPDATE SET tgt.description = src.job_desc,
                tgt.ai_system = src.ai_system,
                tgt.ai_prompt = src.ai_prompt,
                tgt.enabled = src.enabled,
                tgt.pass_fail = src.pass_fail,
                tgt.collection_id = src.collection_id
WHEN NOT MATCHED THEN
INSERT 
           (
            [description]
           ,[ai_system]
           ,[ai_prompt]
           ,[enabled]
           ,[collection_id]
           ,[pass_fail]
           )
VALUES (
        src.job_desc
        ,src.ai_system
        ,src.ai_prompt
        ,src.enabled
        ,src.collection_id
        ,src.pass_fail
        )
;
"""

SQL_INSERT_QUICK_JOB_LOG = """
INSERT INTO [dbo].[QuickJobLog]
           ([job_id]
           ,[message])
     VALUES
           ({job_id}
           ,{message})
"""

SQL_SELECT_WORKFLOW_CATEGORIES = """SELECT * FROM [dbo].[WorkflowCategories]"""

SQL_SELECT_WORKFLOW_CATEGORY = """SELECT * FROM [dbo].[WorkflowCategories] WHERE id = {id}"""

SQL_SELECT_WORKFLOWS = """SELECT w.*, wc.name [category] FROM [dbo].[Workflows] w LEFT JOIN [dbo].[WorkflowCategories] wc ON wc.id = w.category_id"""

SQL_SELECT_WORKFLOW_VARIABLES = """SELECT w.* FROM [dbo].[Workflow_Variables] w WHERE w.workflow_id = {id}"""

SQL_SELECT_WORKFLOW = """SELECT w.*, wc.name [category] FROM [dbo].[Workflows] w LEFT JOIN [dbo].[WorkflowCategories] wc ON wc.id = w.category_id WHERE w.id = {id}"""

SQL_DELETE_WORKFLOW = """DELETE FROM [dbo].[Workflows] WHERE id = {id}"""

SQL_SELECT_GROUPS = """SELECT * FROM [dbo].[Groups]"""

SQL_SELECT_GROUP = """SELECT * FROM [dbo].[Groups] WHERE id = {id}"""

SQL_SELECT_USER_GROUPS = """SELECT * 
FROM [dbo].[UserGroups] g JOIN [dbo].[User] u on u.[id] = g.[user_id] WHERE group_id = {id}"""

SQL_DELETE_GROUPS = """DELETE FROM [dbo].[Groups] WHERE id = {id}"""

SQL_DELETE_QUICK_JOB = """DELETE FROM [dbo].[QuickJob] WHERE id = {job_id}"""

SQL_MAX_QUICK_JOB_SCHEDULE_ID = """SELECT MAX(id) id FROM [dbo].[QuickJobSchedules]"""

SQL_SELECT_QUICK_JOB_SCHEDULE = """SELECT * FROM [dbo].[QuickJobSchedules] WHERE job_id = {job_id}"""

SQL_SELECT_QUICK_JOB_SCHEDULES = """SELECT * FROM [dbo].[QuickJobSchedules]"""

SQL_MERGE_QUICK_JOB_SCHEDULE = """
MERGE INTO [dbo].[QuickJobSchedules] AS Target
USING (VALUES({id}, {job_id}, {task_name}, {start_time}, {frequency}, {enabled})) AS Source (id, job_id, task_name, start_time, frequency, enabled)
ON (Target.id = Source.id)

WHEN MATCHED THEN 
UPDATE SET 
    Target.start_time = Source.start_time,
    Target.frequency = Source.frequency,
    Target.enabled = Source.enabled,
    Target.create_date = getutcdate()

WHEN NOT MATCHED BY TARGET THEN 
INSERT (job_id, task_name, start_time, frequency, enabled, create_date) 
VALUES (Source.job_id, Source.task_name, Source.start_time, Source.frequency, Source.enabled, getutcdate());
"""

SQL_MERGE_GROUPS = """
MERGE [dbo].[Groups] AS tgt
USING (VALUES
           ({id}
            ,{group_name}
           )) AS src (id, group_name)
           ON tgt.id = src.id
WHEN MATCHED THEN
    UPDATE SET tgt.group_name = src.group_name
WHEN NOT MATCHED THEN
INSERT 
           (
            [group_name]
           )
VALUES (
        src.group_name
        )
;
"""

SQL_MERGE_QUICK_JOB = """
MERGE [dbo].[QuickJob] AS tgt
USING (VALUES
           ({job_id}
            ,{job_desc}
           ,{ai_system}
           ,{enabled}
           ,{collection_id}
           ,{agent_id}
           )) AS src (job_id, job_desc, ai_system, enabled, collection_id, agent_id)
           ON tgt.id = src.job_id
WHEN MATCHED THEN
    UPDATE SET tgt.description = src.job_desc,
                tgt.ai_system = src.ai_system,
                tgt.enabled = src.enabled,
                tgt.collection_id = src.collection_id,
                tgt.agent_id = src.agent_id
WHEN NOT MATCHED THEN
INSERT 
           (
            [description]
           ,[ai_system]
           ,[enabled]
           ,[collection_id]
           ,[agent_id]
           )
VALUES (
        src.job_desc
        ,src.ai_system
        ,src.enabled
        ,src.collection_id
        ,src.agent_id
        )
;
"""

SQL_INSERT_JOB_DETAIL = """
INSERT INTO [dbo].[JobDetail]
           (
            [id]
           ,[description]
           ,[fn_type]
           ,[fn_text]
           ,[enabled]
           ,[create_date]
           ,[fn_pass_type]
           ,[fn_pass_text]
           ,[fn_fail_type]
           ,[fn_fail_text]
           ,[fn_finish_type]
           ,[fn_finish_text]
           )
VALUES (
        {job_id}
        ,''
        ,{fn_type}
        ,{fn_text}
        ,1
        ,getutcdate()
        ,NULL
        ,NULL
        ,NULL
        ,NULL
        ,{fn_finish_type}
        ,{fn_finish_text}
        )
"""

SQL_MERGE_JOB_DETAIL = """
MERGE [dbo].[JobDetail] AS tgt
USING (VALUES
           ({job_id}
            ,{fn_type}
           ,{fn_text}
            --,{fn_pass_type}
           --,{fn_pass_text}
            --,{fn_fail_type}
           --,{fn_fail_text}
           ,{fn_finish_type}
           ,{fn_finish_text}
           )) AS src (job_id, fn_type, fn_text, /*fn_pass_type, fn_pass_text, fn_fail_type, fn_fail_text,*/ fn_finish_type, fn_finish_text)
           ON tgt.id = src.job_id
WHEN MATCHED THEN
    UPDATE SET tgt.fn_type = src.fn_type,
                tgt.fn_text = src.fn_text,
                --tgt.fn_pass_type = src.fn_pass_type,
                --tgt.fn_pass_text = src.fn_pass_text,
                --tgt.fn_fail_type = src.fn_fail_type,
                --tgt.fn_fail_text = src.fn_fail_text,
                tgt.fn_finish_type = src.fn_finish_type,
                tgt.fn_finish_text = src.fn_finish_text
WHEN NOT MATCHED THEN
INSERT 
           (
            [id]
           ,[description]
           ,[fn_type]
           ,[fn_text]
           ,[enabled]
           ,[create_date]
           --,[fn_pass_type]
           --,[fn_pass_text]
           --,[fn_fail_type]
           --,[fn_fail_text]
           ,[fn_finish_type]
           ,[fn_finish_text]
           )
VALUES (
        src.job_id
        ,''
        ,src.fn_type
        ,src.fn_text
        ,1
        ,getutcdate()
        --,src.fn_pass_type
        --,src.fn_pass_text
        --,src.fn_fail_type
        --,src.fn_fail_text
        ,src.fn_finish_type
        ,src.fn_finish_text
        )
;
"""

SQL_MAX_JOB_ID = """
SELECT MAX(id) job_id
FROM [dbo].[JobHeader]
"""

SQL_MAX_QUICK_JOB_ID = """
SELECT MAX(id) job_id
FROM [dbo].[QuickJob]
"""

SQL_MAX_COLLECTION_ID = """
SELECT MAX(id) collection_id
FROM [dbo].[JobCollection]
"""

SQL_MAX_USER_ID = """
SELECT MAX(id) id
FROM [dbo].[User]
"""


SQL_MAX_CONNECTION_ID = """
SELECT MAX(id) connection_id
FROM [dbo].[Connections]
"""

SQL_SELECT_ALL_JOBS = """
 SELECT h.*
		,[fn_type]
		,[fn_text]
		,[fn_pass_type]
		,[fn_pass_text]
		,[fn_fail_type]
		,[fn_fail_text]
		,[fn_finish_type]
		,[fn_finish_text]
 FROM [dbo].[JobHeader] h
 JOIN [dbo].[JobDetail] d ON d.id = h.id 
"""

SQL_SELECT_ALL_COLLECTIONS = """
SELECT * FROM [dbo].[JobCollection]
"""

SQL_SELECT_ALL_CONNECTIONS = """
SELECT * FROM [dbo].[Connections]
"""

SQL_SELECT_ALL_USERS = """
SELECT * FROM [dbo].[User]
"""

SQL_SELECT_USER = """
SELECT * FROM [dbo].[User] WHERE id = {user_id}
"""

SQL_SELECT_USER_BY_USER_NAME = """
SELECT * FROM [dbo].[User] WHERE user_name = {user_name}
"""


SQL_SELECT_JOB = """
 SELECT h.*
		,[fn_type]
		,[fn_text]
		,[fn_pass_type]
		,[fn_pass_text]
		,[fn_fail_type]
		,[fn_fail_text]
		,[fn_finish_type]
		,[fn_finish_text]
 FROM [dbo].[JobHeader] h
 JOIN [dbo].[JobDetail] d ON d.id = h.id 
 WHERE h.id = '{job_name}'
"""

SQL_SELECT_COLLECTION = """
SELECT * FROM [dbo].[JobCollection] WHERE id = {collection_id}
"""

SQL_DELETE_COLLECTION = """
DELETE FROM [dbo].[JobCollection] WHERE id = {collection_id}
"""

SQL_SELECT_CONNECTION = """
SELECT * FROM [dbo].[Connections] WHERE id = {connection_id}
"""

SQL_DELETE_CONNECTION = """
DELETE FROM [dbo].[Connections] WHERE id = {connection_id}
"""

SQL_DELETE_USER = "DELETE FROM [dbo].[User] WHERE id = {user_id}"

SQL_MERGE_USER = """
MERGE [dbo].[User] AS tgt
USING (VALUES
           ({user_id}
            ,{user_name}
            ,{role}
            ,{email}
            ,{phone}
            ,{name}
            ,{password}
            ,{auth_provider}
            ,{external_id}
           )) AS src (user_id, user_name, role, email, phone, name, password, auth_provider, external_id)
           ON tgt.id = src.user_id
WHEN MATCHED THEN
    UPDATE SET
                tgt.user_name = src.user_name,
                tgt.role = src.role,
                tgt.email = src.email,
                tgt.phone = src.phone,
                tgt.name = src.name,
                tgt.password = CASE WHEN src.password = '' THEN tgt.password ELSE src.password END,
                tgt.auth_provider = CASE WHEN src.auth_provider = '' THEN tgt.auth_provider ELSE src.auth_provider END,
                tgt.external_id = CASE WHEN src.external_id = '' THEN tgt.external_id ELSE src.external_id END
WHEN NOT MATCHED THEN
INSERT
           (
            [user_name],
            [role],
            [email],
            [phone],
            [name],
            [password],
            [auth_provider],
            [external_id]
           )
VALUES (
        src.user_name,
        src.role,
        src.email,
        src.phone,
        src.name,
        src.password,
        CASE WHEN src.auth_provider = '' THEN 'local' ELSE src.auth_provider END,
        CASE WHEN src.external_id = '' THEN NULL ELSE src.external_id END
        )
;
"""

# Identity Provider Config queries
SQL_SELECT_ALL_IDENTITY_PROVIDERS = """
SELECT * FROM [dbo].[IdentityProviderConfig]
"""

SQL_SELECT_IDENTITY_PROVIDER = """
SELECT * FROM [dbo].[IdentityProviderConfig] WHERE id = {provider_id}
"""

SQL_SELECT_IDENTITY_PROVIDERS_BY_TYPE = """
SELECT * FROM [dbo].[IdentityProviderConfig] WHERE provider_type = {provider_type}
"""

SQL_SELECT_ENABLED_IDENTITY_PROVIDERS = """
SELECT * FROM [dbo].[IdentityProviderConfig] WHERE is_enabled = 1
"""

SQL_DELETE_IDENTITY_PROVIDER = "DELETE FROM [dbo].[IdentityProviderConfig] WHERE id = {provider_id}"

SQL_MERGE_IDENTITY_PROVIDER = """
MERGE [dbo].[IdentityProviderConfig] AS tgt
USING (VALUES
           ({provider_id}
            ,{provider_type}
            ,{provider_name}
            ,{is_enabled}
            ,{is_default}
            ,{config_json}
            ,{auto_provision}
            ,{default_role}
            ,{group_role_mapping}
           )) AS src (provider_id, provider_type, provider_name, is_enabled, is_default, config_json, auto_provision, default_role, group_role_mapping)
           ON tgt.id = src.provider_id
WHEN MATCHED THEN
    UPDATE SET
                tgt.provider_type = src.provider_type,
                tgt.provider_name = src.provider_name,
                tgt.is_enabled = src.is_enabled,
                tgt.is_default = src.is_default,
                tgt.config_json = src.config_json,
                tgt.auto_provision = src.auto_provision,
                tgt.default_role = src.default_role,
                tgt.group_role_mapping = src.group_role_mapping,
                tgt.updated_at = GETDATE()
WHEN NOT MATCHED THEN
INSERT
           (
            [provider_type],
            [provider_name],
            [is_enabled],
            [is_default],
            [config_json],
            [auto_provision],
            [default_role],
            [group_role_mapping]
           )
VALUES (
        src.provider_type,
        src.provider_name,
        src.is_enabled,
        src.is_default,
        src.config_json,
        src.auto_provision,
        src.default_role,
        src.group_role_mapping
        )
;
"""


SQL_MERGE_COLLECTION = """
MERGE [dbo].[JobCollection] AS tgt
USING (VALUES
           ({collection_id}
            ,{collection_name}
           )) AS src (id, collection_name)
           ON tgt.id = src.id
WHEN MATCHED THEN
    UPDATE SET 
                tgt.collection_name = src.collection_name
WHEN NOT MATCHED THEN
INSERT 
           (
            [collection_name]
           )
VALUES (
        src.collection_name
        )
;
"""

SQL_MERGE_CONNECTION = """
MERGE [dbo].[Connections] AS tgt
USING (VALUES
           ({connection_id}
			,{connection_name}
            ,{server}
            ,{port}
			,{database_name}
            ,{database_type}
			,{user_name}
			,{password}
            ,{parameters}
            ,{connection_string}
            ,{odbc_driver}
            ,{instance_url}
            ,{token}
            ,{api_key}
            ,{dsn}
           )) AS src ([connection_id], [connection_name], [server], [port], [database_name], [database_type], [user_name], [password], [parameters], [connection_string], [odbc_driver], [instance_url], [token], [api_key], [dsn])
           ON tgt.id = src.connection_id
WHEN MATCHED THEN
    UPDATE SET 
                tgt.[connection_name] = src.connection_name,
				tgt.server = src.server,
                tgt.port = src.port,
				tgt.database_name = src.database_name,
                tgt.database_type = src.database_type,
				tgt.user_name = src.user_name,
				tgt.password = src.password,
                tgt.parameters = src.parameters,
                tgt.connection_string = case when src.connection_string = '' or src.connection_string = 'None' then NULL else src.connection_string end,
                tgt.odbc_driver = src.odbc_driver,
                tgt.instance_url = src.instance_url,
                tgt.token = src.token,
                tgt.api_key = src.api_key,
                tgt.dsn = src.dsn
WHEN NOT MATCHED THEN
INSERT 
           (
            [connection_name],
			[server],
            [port],
			[database_name],
            [database_type],
			[user_name],
			[password], 
            [parameters], 
            [connection_string],
            [odbc_driver],
            [instance_url],
            [token],
            [api_key],
            [dsn]
           )
VALUES (
        src.connection_name,
		src.server,
        src.port,
		src.database_name,
        src.database_type,
		src.user_name,
		src.password,
		src.parameters,
		case when src.connection_string = '' or src.connection_string = 'None' then NULL else src.connection_string end,
        src.odbc_driver,
        src.instance_url,
        src.token,
        src.api_key,
        src.dsn
        )
;
"""

SQL_SELECT_ALL_QUICK_JOBS = """
 SELECT h.*
 FROM [dbo].[QuickJob] h
 WHERE agent_id IN (select agent_id  from [dbo].[UserGroups] ug
 join [dbo].[AgentGroups] ag on ag.group_id = ug.group_id
 where [user_id] = {user_id})
"""

SQL_SELECT_QUICK_JOB = """
 SELECT h.*
 FROM [dbo].[QuickJob] h
 WHERE agent_id IN (select agent_id  from [dbo].[UserGroups] ug
 join [dbo].[AgentGroups] ag on ag.group_id = ug.group_id
 where [user_id] = {user_id})
 AND h.id = '{job_name}'
"""

SQL_SELECT_ALL_QUICK_JOBS_EXE = """
 SELECT h.*
 FROM [dbo].[QuickJob] h
"""

SQL_SELECT_QUICK_JOB_EXE = """
 SELECT h.*
 FROM [dbo].[QuickJob] h
 WHERE h.id = '{job_name}'
"""

SQL_SELECT_JOB_STATUS = """
SELECT TOP 1 [Job_ID]
      ,[Job_Name]
      ,[Job_Step_ID]
      ,[Job_Step]
      ,[Status]
      ,[Run_Date]
      ,[Run_Time]
      ,[Run_Duration]
      ,[Server]
  FROM [dbo].[vw_JOB_STATUS_CURRENT_DAY]
  WHERE Job_Name LIKE '%{job_name}%'
    ORDER BY [Run_Time] DESC
"""

SQL_SELECT_JOB_STATUS_ALL = """
SELECT [Job_ID]
      ,[Job_Name]
      ,[Job_Step_ID]
      ,[Job_Step]
      ,[Status]
      ,[Run_Date]
      ,[Run_Time]
      ,[Run_Duration]
      ,[Server]
  FROM [dbo].[vw_JOB_STATUS_CURRENT_DAY]
  WHERE Job_Name IN ({job_name})
    ORDER BY [Run_Time] DESC
"""

SQL_SELECT_LOG = """
SELECT a.[id]
      ,[created_at]
      ,[level_name]
      ,[message]
      ,[module]
      ,[func_name]
      ,[line_no]
      ,[job_id]
      ,[date]
	  ,CASE WHEN a.job_id = 999 THEN 'Monitoring Chat' ELSE q.description END [Job Name]
  FROM [dbo].[app_log] a
  LEFT JOIN dbo.QuickJob q ON q.id = a.job_id
  WHERE convert(date, [date]) = {date} 
"""

SQL_SELECT_QUICKJOB_LOG = """
SELECT l.[id]
      ,l.[date]
      ,l.[job_id]
	  ,j.description [job_name]
      ,l.[message]
  FROM [dbo].[QuickJobLog] l
  LEFT JOIN dbo.QuickJob j ON j.id = l.job_id
  WHERE l.job_id = {job_id}
  AND CAST(DATEADD(MINUTE, -{timezone_offset_minutes}, CAST(l.[date] AS datetime)) AS date) = CAST({date} AS date)
"""

SQL_MERGE_TABLE = """
MERGE [dbo].[llm_Tables] AS tgt
USING (VALUES
           ({id}
            ,{table_name}
           ,{table_description}
           ,{connection_id}
           )) AS src (id, table_name, table_description, connection_id)
           ON tgt.id = src.id
WHEN MATCHED THEN
    UPDATE SET tgt.table_name = src.table_name,
                tgt.table_description = src.table_description,
                tgt.connection_id = src.connection_id
WHEN NOT MATCHED THEN
INSERT 
           (
            [table_name]
           ,[table_description]
           ,[connection_id]
           )
VALUES (
        src.table_name
        ,src.table_description
        ,src.connection_id
        )
;
"""

SQL_SELECT_TABLES = """SELECT * FROM [dbo].[llm_Tables] WHERE connection_id = {connection_id}"""

SQL_SELECT_COLUMNS = """SELECT * FROM [dbo].[llm_Columns] WHERE table_id = {table_id}"""

SQL_DELETE_TABLE = """DELETE FROM [dbo].[llm_Tables] WHERE id = {table_id}"""

SQL_DELETE_COLUMN = """DELETE FROM [dbo].[llm_Columns] WHERE id = {id}"""

SQL_DELETE_TABLE_COLUMNS = """DELETE FROM [dbo].[llm_Columns] WHERE table_id = {table_id}"""

SQL_MAX_TABLE_ID = """
SELECT MAX(id) id
FROM [dbo].[llm_Tables]
"""

SQL_MERGE_COLUMN = """
MERGE [dbo].[llm_Columns] AS tgt
USING (VALUES
           ({id}
           ,{table_id}
           ,{column_name}
           ,{column_description}
           ,{column_values}
           )) AS src (id, table_id, column_name, column_description, column_values)
           ON tgt.id = src.id
WHEN MATCHED THEN
    UPDATE SET tgt.column_name = src.column_name,
                tgt.column_description = src.column_description,
                tgt.column_values = src.column_values
WHEN NOT MATCHED THEN
INSERT 
           (
            [table_id]
           ,[column_name]
           ,[column_description]
           ,[column_values]
           )
VALUES (
        src.table_id
        ,src.column_name
        ,src.column_description
        ,src.column_values
        )
;
"""

SQL_MAX_COLUMN_ID = """
SELECT MAX(id) id
FROM [dbo].[llm_Columns]
"""

SYS_SQL_SELECT_TABLE_DESCRIPTIONS = """
SELECT [table_name]
      ,[table_description]
  FROM [dbo].[llm_Tables]
  WHERE connection_id = {connection_id}
"""

SYS_SQL_SELECT_COLUMN_DESCRIPTIONS = """
SELECT t.[table_name]
      ,[column_name]
      ,[column_description]
      ,[column_values]
  FROM [dbo].[llm_Columns] c
  JOIN [dbo].[llm_Tables] t ON t.[id] = c.[table_id]
WHERE t.[table_name] IN ({table_list})
AND t.connection_id = {connection_id}
"""

SYS_SQL_SELECT_COLUMN_WITH_TABLE_DESCRIPTIONS = """
SELECT t.[table_name]
      ,[column_name]
      ,[column_description]
      ,[column_values]
      ,t.[table_description]
  FROM [dbo].[llm_Columns] c
  JOIN [dbo].[llm_Tables] t ON t.[id] = c.[table_id]
WHERE t.[table_name] IN ({table_list})
AND t.connection_id = {connection_id}
"""

SYS_SQL_SELECT_COLUMN_DESCRIPTIONS_ALL = """
SELECT t.[table_name]
      ,[column_name]
      ,[column_description]
      ,[column_values]
  FROM [dbo].[llm_Columns] c
  JOIN [dbo].[llm_Tables] t ON t.[id] = c.[table_id]
WHERE t.connection_id = {connection_id}
"""

SYS_SQL_SELECT_ACCESS_TO_AGENTS_BY_EMAIL = """
SELECT a.*
FROM Agents a
INNER JOIN AgentGroups ag ON a.id = ag.agent_id
INNER JOIN UserGroups ug ON ag.group_id = ug.group_id
INNER JOIN [User] u ON ug.user_id = u.id
WHERE u.email = {email}
"""


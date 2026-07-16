@echo off
rem ============================================================================
rem  Start_SFTP_Server.bat (pack 09) - double-click to start the local SFTP
rem  test server needed by Code_Flows_Test_Script.docx (prerequisite 0.3).
rem  Thin wrapper: the actual server + launcher live in ..\_sftp_test_server.
rem  Serves sftp://testuser:testpass@127.0.0.1:2222 (matches secret AUTODEMO_SFTP).
rem ============================================================================
call "%~dp0..\_sftp_test_server\Start_SFTP_Server.bat"

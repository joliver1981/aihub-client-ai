[Setup]
AppId={{999ECAE8-60BF-4566-B61D-51F5BFAC7B66}
AppName=AIHub
AppVersion=1.7
AppPublisher=EveriAI, LLC.
AppPublisherURL=https://www.everiai.ai/
AppSupportURL=https://www.everiai.ai/
AppUpdatesURL=https://github.com/everiai-aihub/releases
DefaultDirName={autopf}\AIHub
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DefaultGroupName=AIHub
DisableProgramGroupPage=yes
LicenseFile=C:\src\aihub-client-ai-dev\static\license.txt
OutputBaseFilename=AIHub.Setup.v1.7
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; For auto-updates - we handle service stopping ourselves
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
; These are defaults - CurPageChanged overrides them dynamically with the correct port
FinishedLabelNoIcons=Setup has finished installing [name] on your computer.%n%nAI Hub is a web-based application that runs locally on your computer.%n%nServices are now starting. Your browser will open automatically when ready.
FinishedLabel=Setup has finished installing [name] on your computer.%n%nAI Hub is a web-based application that runs locally on your computer.%n%nServices are now starting. Your browser will open automatically when ready.

[Dirs]
Name: "{app}\cache"
Name: "{app}\logs"
Name: "{app}\tools"
Name: "{app}\tmp"
Name: "{app}\data"
Name: "{app}\updates"
Name: "{app}\agent_environments"
Name: "{app}\agent_environments\python-bundle"
Name: "{app}\agent_environments\python-bundle-requirements"
Name: "{app}\static\icons"
Name: "{app}\integrations"

[Files]
; =============================================================================
; ONEDIR: Each service is a folder with exe + dependencies (13 services total)
; Source: aihub-client-ai-dev (AI DEV build)
; =============================================================================
Source: "C:\src\aihub-client-ai-dev\dist\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
;Source: "C:\src\aihub-client-ai-dev\dist\ExecuteQuickJob\*"; DestDir: "{app}\ExecuteQuickJob"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "C:\src\aihub-client-ai-dev\dist\document_api_server\*"; DestDir: "{app}\document_api_server"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "C:\src\aihub-client-ai-dev\dist\document_job_processor\*"; DestDir: "{app}\document_job_processor"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "C:\src\aihub-client-ai-dev\dist\job_scheduler_service\*"; DestDir: "{app}\job_scheduler_service"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "C:\src\aihub-client-ai-dev\dist\wsgi_vector_api\*"; DestDir: "{app}\wsgi_vector_api"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "C:\src\aihub-client-ai-dev\dist\wsgi_agent_api\*"; DestDir: "{app}\wsgi_agent_api"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "C:\src\aihub-client-ai-dev\dist\wsgi_knowledge_api\*"; DestDir: "{app}\wsgi_knowledge_api"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "C:\src\aihub-client-ai-dev\dist\wsgi_executor_service\*"; DestDir: "{app}\wsgi_executor_service"; Flags: ignoreversion recursesubdirs createallsubdirs
; MCP Gateway, Builder Service, Builder Data, Cloud Gateway, Command Center
Source: "C:\src\aihub-client-ai-dev\dist\mcp_gateway\*"; DestDir: "{app}\mcp_gateway"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "C:\src\aihub-client-ai-dev\dist\builder_service\*"; DestDir: "{app}\builder_service"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "C:\src\aihub-client-ai-dev\dist\builder_data\*"; DestDir: "{app}\builder_data"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "C:\src\aihub-client-ai-dev\dist\cloud_gateway\*"; DestDir: "{app}\cloud_gateway"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "C:\src\aihub-client-ai-dev\dist\command_center_service\*"; DestDir: "{app}\command_center_service"; Flags: ignoreversion recursesubdirs createallsubdirs

; Non-PyInstaller files (unchanged)
Source: "C:\src\nssm-2.24\win64\nssm.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\src\aihub-client-ai-dev\dist\.env"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "C:\src\aihub-client-ai-dev\dist\core_tools.yaml"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\src\aihub-client-ai-dev\dist\user_config.py"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "C:\src\aihub-client-ai-dev\dist\user_prompts.py"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "C:\src\aihub-client-ai-dev\dist\GeneralAgent.pyd"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\src\aihub-client-ai-dev\assets\prompt_templates\*"; DestDir: "{app}\app\assets\prompt_templates"; Flags: recursesubdirs createallsubdirs
; Include bundled Python with installation
Source: "C:\src\aihub-client-ai-dev\dist\python-bundle\*"; DestDir: "{app}\agent_environments\python-bundle"; Flags: ignoreversion recursesubdirs
Source: "C:\src\aihub-client-ai-dev\dist\python-bundle-requirements\*"; DestDir: "{app}\agent_environments\python-bundle-requirements"; Flags: ignoreversion recursesubdirs
Source: "C:\src\aihub-client-ai-dev\dist\static\icons\*"; DestDir: "{app}\static\icons"; Flags: ignoreversion recursesubdirs
Source: "C:\src\aihub-client-ai-dev\integrations\*"; DestDir: "{app}\integrations"; Flags: ignoreversion recursesubdirs
; Secure configuration loader and credential seeding script
Source: "C:\src\aihub-client-ai-dev\secure_config.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\src\aihub-client-ai-dev\seed_credentials.py"; DestDir: "{app}"; Flags: ignoreversion

[Registry]
; Store API key securely in the registry (ACL restricted to Administrators + SYSTEM)
; The value is written by CurStepChanged after validation - this section reserves the key path
Root: HKLM; Subkey: "Software\AI Hub\Config"; Flags: uninsdeletekeyifempty

[Icons]
; ONEDIR: Updated paths to exe inside subfolder
Name: "{group}\AIHub"; Filename: "{app}\app\app.exe"
; Browser shortcuts are created programmatically in CurStepChanged to use the user-configured port
Name: "{group}\{cm:UninstallProgram,AIHub}"; Filename: "{uninstallexe}"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

; [Run] section removed - browser launch is now handled in CurStepChanged
; to use the user-configured port instead of a hardcoded value

[Code]
var
  CustomPage: TWizardPage;
  ApiKeyEdit, PortEdit, LocalUserEdit, LocalPwdEdit, LocalDomainEdit, RemoteUserEdit, RemotePwdEdit, RemoteDomainEdit: TEdit;
  ReadOnlyCheckBox: TCheckBox;
  InfoLabel: TLabel;
  ApiKeyLabel, PortLabel, LocalUserLabel, LocalPwdLabel, LocalDomainLabel, RemoteUserLabel, RemotePwdLabel, RemoteDomainLabel: TLabel;
  ValidateButton: TButton;
  IsUpgrade: Boolean;
  ExistingApiKey: String;
  ExistingInstallPath: String;
  ConfiguredPort: String;
  // Cached service account credentials (read from .env early for use during service install)
  CachedSvcUser, CachedSvcPwd, CachedSvcDomain: String;

const
  APIValidationURL = 'https://ai-hub-api.azurewebsites.net/validate_license';

function IsNewInstallation(): Boolean;
begin
  Result := not IsUpgrade;
end;

function GetInstalledVersion(): String;
var
  RegValue: String;
begin
  Result := '';
  if RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{999ECAE8-60BF-4566-B61D-51F5BFAC7B66}_is1',
    'DisplayVersion', RegValue) then
    begin
      Result := RegValue;
      MsgBox('Existing installation detected. AI Hub will be upgraded from ' + Result + ' to ' + '{#SetupSetting("AppVersion")}', mbInformation, MB_OK);
    end;

    if Result = '' then
    begin
      if FileExists('c:\Program Files\AIHub\.env') then
        Result := ExpandConstant('{#SetupSetting("AppVersion")}');
    end;
end;

function GetInstallPath(): String;
var
  InstallPath: String;
begin
  Result := '';
  if RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{999ECAE8-60BF-4566-B61D-51F5BFAC7B66}_is1',
    'InstallLocation', InstallPath) then
    Result := InstallPath;
end;

function ReadEnvFileFromPath(const FilePath: String; const Key: String): String;
var
  Lines: TArrayOfString;
  I: Integer;
  Line: String;
  KeyValue: String;
begin
  Result := '';

  if FileExists(FilePath) then
  begin
    if LoadStringsFromFile(FilePath, Lines) then
    begin
      for I := 0 to GetArrayLength(Lines) - 1 do
      begin
        Line := Trim(Lines[I]);
        if (Pos(Key + '=', Line) = 1) then
        begin
          KeyValue := Copy(Line, Length(Key) + 2, Length(Line));
          Result := KeyValue;
          Break;
        end;
      end;
    end;
  end;
end;

function GetConfiguredPort(): String;
var
  EnvPath: String;
  PortValue: String;
begin
  Result := '5001';  // Default port

  EnvPath := ExpandConstant('{app}\.env');
  if FileExists(EnvPath) then
  begin
    PortValue := ReadEnvFileFromPath(EnvPath, 'HOST_PORT');
    if PortValue <> '' then
      Result := PortValue;
  end;

  Log('Configured port: ' + Result);
  ConfiguredPort := Result;
end;

function EnsureEnvKeyExists(const FilePath, Key, Value: String): Boolean;
var
  Existing: String;
  LineToAdd: String;
begin
  // Returns True if the key already existed or was successfully appended.
  // Returns False only if we attempted to append and failed.
  Result := True;

  Existing := ReadEnvFileFromPath(FilePath, Key);
  if Existing <> '' then
  begin
    Log('Env key already present: ' + Key + '=' + Existing);
    Exit; // nothing to do
  end;

  Log('Env key missing, appending: ' + Key + '=' + Value);
  LineToAdd := #13#10 + Key + '=' + Value + #13#10;
  if not SaveStringToFile(FilePath, LineToAdd, True) then
  begin
    Log('ERROR: Failed to append ' + Key + ' to ' + FilePath);
    Result := False;
  end
  else
    Log('Successfully appended ' + Key + ' to ' + FilePath);
end;

procedure ReadOnlyCheckBoxClick(Sender: TObject);
begin
  LocalUserEdit.ReadOnly := ReadOnlyCheckBox.Checked;
  LocalPwdEdit.ReadOnly := ReadOnlyCheckBox.Checked;
  LocalDomainEdit.ReadOnly := ReadOnlyCheckBox.Checked;

  if ReadOnlyCheckBox.Checked then
  begin
    LocalUserEdit.Color := clBtnFace;
    LocalPwdEdit.Color := clBtnFace;
    LocalDomainEdit.Color := clBtnFace;
  end
  else
  begin
    LocalUserEdit.Color := clWindow;
    LocalPwdEdit.Color := clWindow;
    LocalDomainEdit.Color := clWindow;
  end;
end;

function ValidateApiKey(const ApiKey: string): Boolean;
var
  WinHttpReq: Variant;
  ResponseText: string;
begin
  Result := False;
  try
    WinHttpReq := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    WinHttpReq.Open('GET', APIValidationURL + '/' + ApiKey, False);
    WinHttpReq.Send('');
    ResponseText := WinHttpReq.ResponseText;
    Result := (WinHttpReq.Status = 200) and (Pos('"response":"valid"', ResponseText) > 0);
  except
    MsgBox('Error validating API key.', mbError, MB_OK);
  end;
end;

procedure ValidateButtonClick(Sender: TObject);
begin
  if ValidateApiKey(ApiKeyEdit.Text) then
  begin
    MsgBox('API Key is valid.', mbInformation, MB_OK);
  end
  else
  begin
    MsgBox('Invalid API Key.', mbError, MB_OK);
  end;
end;

procedure WriteApiKeyToRegistry(const ApiKey: String);
begin
  if RegWriteStringValue(HKLM, 'Software\AI Hub\Config', 'ApiKey', ApiKey) then
    Log('API_KEY written to registry successfully')
  else
    Log('WARNING: Failed to write API_KEY to registry');
end;

procedure ConfigureServiceRecovery(ServiceName: String);
var
  ResultCode: Integer;
begin
  // Configure Windows Service Recovery Options via SC command
  Exec('sc.exe', 'failure "' + ServiceName + '" reset= 86400 actions= restart/60000/restart/300000/restart/600000',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  // Set service to restart on non-zero exit codes
  Exec('sc.exe', 'failureflag "' + ServiceName + '" 1', '',
    SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure DisableServiceRecovery(ServiceName: String);
var
  ResultCode: Integer;
begin
  // Disable recovery actions before stopping service
  // This prevents Windows from auto-restarting the service during upgrade
  Exec('sc.exe', 'failure "' + ServiceName + '" reset= 0 actions= ""',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('sc.exe', 'failureflag "' + ServiceName + '" 0', '',
    SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// =============================================================================
// FIXED v3: Proper service cleanup for NSSM-managed services
// Updated: 13 services total (core 8 + MCP Gateway, Cloud Gateway,
//          Builder Service, Builder Data, Command Center)
//
// The correct order is:
// 1. Disable service recovery (prevents auto-restart during upgrade)
// 2. Stop services via NSSM (graceful shutdown)
// 3. Remove services via NSSM (cleans up service registration)
// 4. Kill any orphan processes AFTER services are removed
// =============================================================================
procedure StopAndRemoveServices();
var
  ResultCode: Integer;
  Services: array[0..12] of String;
  Executables: array[0..12] of String;
  NssmPath: String;
  I: Integer;
begin
  Services[0] := 'AIHub';
  Services[1] := 'AIHubDocAPI';
  Services[2] := 'AIHubDocQueue';
  Services[3] := 'AIHubJobScheduler';
  Services[4] := 'AIHubVectorAPI';
  Services[5] := 'AIHubAgentAPI';
  Services[6] := 'AIHubKnowledgeAPI';
  Services[7] := 'AIHubExecutorService';
  Services[8] := 'AIHubMCPGateway';
  Services[9] := 'AIHubBuilderService';
  Services[10] := 'AIHubBuilderData';
  Services[11] := 'AIHubCloudGateway';
  Services[12] := 'AIHubCommandCenter';

  // Corresponding executable names for taskkill
  Executables[0] := 'app.exe';
  Executables[1] := 'document_api_server.exe';
  Executables[2] := 'document_job_processor.exe';
  Executables[3] := 'job_scheduler_service.exe';
  Executables[4] := 'wsgi_vector_api.exe';
  Executables[5] := 'wsgi_agent_api.exe';
  Executables[6] := 'wsgi_knowledge_api.exe';
  Executables[7] := 'wsgi_executor_service.exe';
  Executables[8] := 'app_mcp_gateway.exe';
  Executables[9] := 'builder_service.exe';
  Executables[10] := 'builder_data.exe';
  Executables[11] := 'app_cloud_gateway.exe';
  Executables[12] := 'command_center_service.exe';
  // ONEDIR: Executables remain the same name, just in subfolders

  Log('========================================');
  Log('STOPPING AND REMOVING SERVICES (v3)');
  Log('========================================');

  // Determine NSSM path - try install path first, then default
  NssmPath := AddBackslash(ExistingInstallPath) + 'nssm.exe';
  if not FileExists(NssmPath) then
  begin
    NssmPath := 'C:\Program Files\AIHub\nssm.exe';
    Log('NSSM not found at install path, trying default: ' + NssmPath);
  end;

  // =========================================================================
  // PHASE 1: Disable service recovery to prevent auto-restart
  // =========================================================================
  Log('Phase 1: Disabling service recovery...');
  for I := 0 to 12 do
  begin
    DisableServiceRecovery(Services[I]);
  end;
  Sleep(1000);

  // =========================================================================
  // PHASE 2: Stop services gracefully via NSSM (or sc.exe fallback)
  // =========================================================================
  Log('Phase 2: Stopping services...');
  if FileExists(NssmPath) then
  begin
    Log('Using NSSM to stop services: ' + NssmPath);
    for I := 0 to 12 do
    begin
      Log('Stopping service: ' + Services[I]);
      Exec(NssmPath, 'stop ' + Services[I], '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Log('  NSSM stop result: ' + IntToStr(ResultCode));
    end;
  end
  else
  begin
    Log('WARNING: NSSM not found, using sc.exe to stop services');
    for I := 0 to 12 do
    begin
      Log('Stopping service: ' + Services[I]);
      Exec('sc.exe', 'stop "' + Services[I] + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Log('  SC stop result: ' + IntToStr(ResultCode));
    end;
  end;

  // Give services time to stop gracefully
  Log('Waiting 5 seconds for services to stop gracefully...');
  Sleep(5000);

  // =========================================================================
  // PHASE 3: Remove service registrations via NSSM (or sc.exe fallback)
  // This MUST happen before killing processes, as it properly cleans up NSSM
  // =========================================================================
  Log('Phase 3: Removing service registrations...');
  if FileExists(NssmPath) then
  begin
    for I := 0 to 12 do
    begin
      Log('Removing service: ' + Services[I]);
      Exec(NssmPath, 'remove ' + Services[I] + ' confirm', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Log('  NSSM remove result: ' + IntToStr(ResultCode));
    end;
  end
  else
  begin
    for I := 0 to 12 do
    begin
      Log('Removing service: ' + Services[I]);
      Exec('sc.exe', 'delete "' + Services[I] + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Log('  SC delete result: ' + IntToStr(ResultCode));
    end;
  end;

  // Wait for service removal to complete
  Sleep(2000);

  // =========================================================================
  // PHASE 4: Final cleanup - kill any orphan processes
  // This is a safety net AFTER services are properly removed
  // =========================================================================
  Log('Phase 4: Final cleanup of any orphan processes...');
  for I := 0 to 12 do
  begin
    Exec('taskkill.exe', '/F /IM ' + Executables[I], '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if ResultCode = 0 then
      Log('  Killed orphan: ' + Executables[I])
    else if ResultCode <> 128 then  // 128 = process not found (expected)
      Log('  ' + Executables[I] + ' taskkill result: ' + IntToStr(ResultCode));
  end;

  // Also clean up any lingering nssm.exe processes
  Exec('taskkill.exe', '/F /IM nssm.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if ResultCode = 0 then
    Log('  Killed orphan nssm.exe processes');

  // Final wait to ensure Windows releases all handles
  Sleep(2000);

  Log('========================================');
  Log('SERVICE CLEANUP COMPLETE');
  Log('========================================');
end;

function InitializeSetup(): Boolean;
var
  OldVersion: String;
  EnvFilePath: String;
begin
  Result := True;
  OldVersion := GetInstalledVersion();
  IsUpgrade := (OldVersion <> '');

  if IsUpgrade then
  begin
    Log('========================================');
    Log('UPGRADE DETECTED');
    Log('========================================');
    Log('Existing version: ' + OldVersion);
    Log('New version: ' + ExpandConstant('{#SetupSetting("AppVersion")}'));

    // Get the existing installation path
    ExistingInstallPath := GetInstallPath();

    if ExistingInstallPath = '' then
    begin
      // Try default location
      ExistingInstallPath := ExpandConstant('{autopf}\AIHub');
      Log('Could not read install path from registry, using default: ' + ExistingInstallPath);
    end
    else
    begin
      Log('Found existing installation path: ' + ExistingInstallPath);
    end;

    // Build path to .env file
    EnvFilePath := AddBackslash(ExistingInstallPath) + '.env';
    Log('Looking for .env file at: ' + EnvFilePath);

    // Try the most common default path
    if not FileExists(EnvFilePath) then
    begin
      Log('File not found, trying the most common path to .env file...');
      EnvFilePath := 'c:\Program Files\AIHub\.env';
    end;

    if not FileExists(EnvFilePath) then
    begin
      MsgBox('Could not find .env file at: ' + EnvFilePath + #13#10#13#10 +
             'The upgrade cannot continue without a valid configuration file.', mbError, MB_OK);
      Log('ERROR: .env file not found');
      Result := False;
      Exit;
    end;

    // Read existing API key — check registry first, then fall back to .env
    if RegQueryStringValue(HKLM, 'Software\AI Hub\Config', 'ApiKey', ExistingApiKey) and (ExistingApiKey <> '') then
    begin
      Log('Successfully read API key from registry (first 8 chars): ' + Copy(ExistingApiKey, 1, 8) + '...');
    end
    else
    begin
      ExistingApiKey := ReadEnvFileFromPath(EnvFilePath, 'API_KEY');
      if ExistingApiKey <> '' then
        Log('Successfully read API key from .env file (first 8 chars): ' + Copy(ExistingApiKey, 1, 8) + '...')
    end;

    if ExistingApiKey = '' then
    begin
      MsgBox('Could not read API_KEY from registry or .env file.' + #13#10#13#10 +
             'Please ensure the installation has a valid API_KEY.', mbError, MB_OK);
      Log('ERROR: API_KEY not found in registry or .env file');
      Result := False;
      Exit;
    end;

    // Cache service account credentials from .env BEFORE migration removes them.
    // These are needed by InstallServices() to configure NSSM ObjectName.
    CachedSvcUser := ReadEnvFileFromPath(EnvFilePath, 'WINTASK_USER');
    CachedSvcPwd := ReadEnvFileFromPath(EnvFilePath, 'WINTASK_PWD');
    CachedSvcDomain := ReadEnvFileFromPath(EnvFilePath, 'LOCAL_DOMAIN');
    Log('Cached service account for upgrade: ' + CachedSvcDomain + '\' + CachedSvcUser);

    // NOTE: Services will be stopped later in PrepareToInstall(),
    // AFTER the user has committed to the installation.
    // This prevents breaking the app if the user cancels the wizard.

  end
  else
  begin
    Log('========================================');
    Log('FRESH INSTALLATION');
    Log('========================================');
  end;
end;

procedure InitializeWizard;
begin
  CustomPage := CreateCustomPage(wpWelcome, 'Configuration', 'Enter your AIHub configuration details');

  // Create and position the labels and controls
  ApiKeyLabel := TLabel.Create(WizardForm);
  ApiKeyLabel.Parent := CustomPage.Surface;
  ApiKeyLabel.Left := 10;
  ApiKeyLabel.Top := 20;
  ApiKeyLabel.Caption := 'License Key:';

  ApiKeyEdit := TEdit.Create(WizardForm);
  ApiKeyEdit.Parent := CustomPage.Surface;
  ApiKeyEdit.Left := 120;
  ApiKeyEdit.Top := ApiKeyLabel.Top - 3;
  ApiKeyEdit.Width := CustomPage.SurfaceWidth - 200;
  ApiKeyEdit.Text := '';

  ValidateButton := TButton.Create(WizardForm);
  ValidateButton.Parent := CustomPage.Surface;
  ValidateButton.Left := ApiKeyEdit.Left + ApiKeyEdit.Width + 10;
  ValidateButton.Top := ApiKeyEdit.Top;
  ValidateButton.Width := 70;
  ValidateButton.Caption := 'Validate';
  ValidateButton.OnClick := @ValidateButtonClick;

  PortLabel := TLabel.Create(WizardForm);
  PortLabel.Parent := CustomPage.Surface;
  PortLabel.Left := 10;
  PortLabel.Top := ApiKeyLabel.Top + 30;
  PortLabel.Caption := 'Port:';

  PortEdit := TEdit.Create(WizardForm);
  PortEdit.Parent := CustomPage.Surface;
  PortEdit.Left := 120;
  PortEdit.Top := PortLabel.Top - 3;
  PortEdit.Width := CustomPage.SurfaceWidth - 130;
  PortEdit.Text := '5001';

  ReadOnlyCheckBox := TCheckBox.Create(WizardForm);
  ReadOnlyCheckBox.Parent := CustomPage.Surface;
  ReadOnlyCheckBox.Left := 10;
  ReadOnlyCheckBox.Top := PortLabel.Top + 30;
  ReadOnlyCheckBox.Width := CustomPage.SurfaceWidth - 20;
  ReadOnlyCheckBox.Caption := 'Use Local System Account (recommended for most users)';
  ReadOnlyCheckBox.OnClick := @ReadOnlyCheckBoxClick;
  // NOTE: Do NOT set .Checked here - wait until all controls are created

  // Informational text about service account and auto-updates
  InfoLabel := TLabel.Create(WizardForm);
  InfoLabel.Parent := CustomPage.Surface;
  InfoLabel.Left := 10;
  InfoLabel.Top := ReadOnlyCheckBox.Top + 24;
  InfoLabel.Width := CustomPage.SurfaceWidth - 20;
  InfoLabel.Height := 52;
  InfoLabel.AutoSize := False;
  InfoLabel.WordWrap := True;
  InfoLabel.Font.Size := 8;
  InfoLabel.Font.Color := clGray;
  InfoLabel.Caption :=
    'Note: If your organization requires services to run under specific credentials, uncheck this box and enter a domain account below.';

  LocalUserLabel := TLabel.Create(WizardForm);
  LocalUserLabel.Parent := CustomPage.Surface;
  LocalUserLabel.Left := 10;
  LocalUserLabel.Top := InfoLabel.Top + InfoLabel.Height + 8;
  LocalUserLabel.Caption := 'Username:';

  LocalUserEdit := TEdit.Create(WizardForm);
  LocalUserEdit.Parent := CustomPage.Surface;
  LocalUserEdit.Left := 120;
  LocalUserEdit.Top := LocalUserLabel.Top - 3;
  LocalUserEdit.Width := CustomPage.SurfaceWidth - 130;
  LocalUserEdit.Text := '';

  LocalPwdLabel := TLabel.Create(WizardForm);
  LocalPwdLabel.Parent := CustomPage.Surface;
  LocalPwdLabel.Left := 10;
  LocalPwdLabel.Top := LocalUserLabel.Top + 30;
  LocalPwdLabel.Caption := 'Password:';

  LocalPwdEdit := TEdit.Create(WizardForm);
  LocalPwdEdit.Parent := CustomPage.Surface;
  LocalPwdEdit.Left := 120;
  LocalPwdEdit.Top := LocalPwdLabel.Top - 3;
  LocalPwdEdit.Width := CustomPage.SurfaceWidth - 130;
  LocalPwdEdit.PasswordChar := '*';
  LocalPwdEdit.Text := '';

  LocalDomainLabel := TLabel.Create(WizardForm);
  LocalDomainLabel.Parent := CustomPage.Surface;
  LocalDomainLabel.Left := 10;
  LocalDomainLabel.Top := LocalPwdLabel.Top + 30;
  LocalDomainLabel.Caption := 'Domain:';

  LocalDomainEdit := TEdit.Create(WizardForm);
  LocalDomainEdit.Parent := CustomPage.Surface;
  LocalDomainEdit.Left := 120;
  LocalDomainEdit.Top := LocalDomainLabel.Top - 3;
  LocalDomainEdit.Width := CustomPage.SurfaceWidth - 130;
  LocalDomainEdit.Text := '';

  // Hidden Remote credentials - values will be copied from Local credentials
  RemoteUserLabel := TLabel.Create(WizardForm);
  RemoteUserLabel.Parent := CustomPage.Surface;
  RemoteUserLabel.Visible := False;

  RemoteUserEdit := TEdit.Create(WizardForm);
  RemoteUserEdit.Parent := CustomPage.Surface;
  RemoteUserEdit.Visible := False;
  RemoteUserEdit.Text := '';

  RemotePwdLabel := TLabel.Create(WizardForm);
  RemotePwdLabel.Parent := CustomPage.Surface;
  RemotePwdLabel.Visible := False;

  RemotePwdEdit := TEdit.Create(WizardForm);
  RemotePwdEdit.Parent := CustomPage.Surface;
  RemotePwdEdit.Visible := False;
  RemotePwdEdit.Text := '';

  RemoteDomainLabel := TLabel.Create(WizardForm);
  RemoteDomainLabel.Parent := CustomPage.Surface;
  RemoteDomainLabel.Visible := False;

  RemoteDomainEdit := TEdit.Create(WizardForm);
  RemoteDomainEdit.Parent := CustomPage.Surface;
  RemoteDomainEdit.Visible := False;
  RemoteDomainEdit.Text := '';

  // NOW it's safe to set the checkbox state and trigger the handler
  // All controls have been created
  ReadOnlyCheckBox.Checked := True;
  ReadOnlyCheckBoxClick(ReadOnlyCheckBox);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // Skip configuration page during upgrades
  Result := IsUpgrade and (PageID = CustomPage.ID);

  if Result then
    Log('Skipping configuration page - this is an upgrade');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  // This function is called AFTER the user clicks Install,
  // but BEFORE files are copied. This is the safe time to stop services.
  Result := '';  // Empty string means continue with installation
  NeedsRestart := False;

  if IsUpgrade then
  begin
    Log('========================================');
    Log('STOPPING SERVICES BEFORE FILE UPDATE');
    Log('========================================');

    // Now it's safe to stop services - user has committed to the upgrade
    StopAndRemoveServices();

    Log('Services stopped successfully - proceeding with file update');
  end;
end;

procedure InstallServices();
var
  ResultCode: Integer;
  EnvConfigFile: String;
  LocalUser, LocalPwd, LocalDomain: String;
  UseSystemAccount: Boolean;
begin
  EnvConfigFile := ExpandConstant('{app}\.env');

  // Read service account settings — use cached values for upgrades
  // (credentials may have already been migrated out of .env by this point)
  if IsUpgrade then
  begin
    Log('Using cached service account credentials for upgrade');
    LocalUser := CachedSvcUser;
    LocalPwd := CachedSvcPwd;
    LocalDomain := CachedSvcDomain;
    UseSystemAccount := (LocalUser = '') or (LocalDomain = '');
    Log('Service will run as: ' + LocalDomain + '\' + LocalUser);
  end
  else
  begin
    Log('Using new installation service configuration');
    LocalUser := LocalUserEdit.Text;
    LocalPwd := LocalPwdEdit.Text;
    LocalDomain := LocalDomainEdit.Text;
    UseSystemAccount := ReadOnlyCheckBox.Checked;
  end;

  WizardForm.StatusLabel.Caption := 'Installing services...';
  Log('Installing AIHub services...');

  // =========================================================================
  // Service 1: Core application
  // =========================================================================
  ShellExec('', ExpandConstant('{app}\nssm.exe'),
    'install AIHub "' + ExpandConstant('{app}\app\app.exe') + '" ' +
    '"--env-file=' + EnvConfigFile + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if not UseSystemAccount then
  begin
    Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHub ObjectName ' + LocalDomain + '\' + LocalUser + ' ' + LocalPwd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Log('AIHub: Set service account to ' + LocalDomain + '\' + LocalUser);
  end;

  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHub Description "AI Hub core service"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  ConfigureServiceRecovery('AIHub');
  Exec(ExpandConstant('{app}\nssm.exe'), 'start AIHub', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('AIHub service started');

  // =========================================================================
  // Service 2: Document API
  // =========================================================================
  ShellExec('', ExpandConstant('{app}\nssm.exe'),
    'install AIHubDocAPI "' + ExpandConstant('{app}\document_api_server\document_api_server.exe') + '" ' +
    '"--env-file=' + EnvConfigFile + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if not UseSystemAccount then
  begin
    Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubDocAPI ObjectName ' + LocalDomain + '\' + LocalUser + ' ' + LocalPwd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubDocAPI Description "AI Hub document API service"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  ConfigureServiceRecovery('AIHubDocAPI');
  Exec(ExpandConstant('{app}\nssm.exe'), 'start AIHubDocAPI', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('AIHubDocAPI service started');

  // =========================================================================
  // Service 3: Document job queue
  // =========================================================================
  ShellExec('', ExpandConstant('{app}\nssm.exe'),
    'install AIHubDocQueue "' + ExpandConstant('{app}\document_job_processor\document_job_processor.exe') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if not UseSystemAccount then
  begin
    Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubDocQueue ObjectName ' + LocalDomain + '\' + LocalUser + ' ' + LocalPwd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubDocQueue Description "AI Hub document job queue service"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  ConfigureServiceRecovery('AIHubDocQueue');
  Exec(ExpandConstant('{app}\nssm.exe'), 'start AIHubDocQueue', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('AIHubDocQueue service started');

  // =========================================================================
  // Service 4: Job scheduler
  // =========================================================================
  ShellExec('', ExpandConstant('{app}\nssm.exe'),
    'install AIHubJobScheduler "' + ExpandConstant('{app}\job_scheduler_service\job_scheduler_service.exe') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if not UseSystemAccount then
  begin
    Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubJobScheduler ObjectName ' + LocalDomain + '\' + LocalUser + ' ' + LocalPwd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubJobScheduler Description "AI Hub job scheduler service"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  ConfigureServiceRecovery('AIHubJobScheduler');
  Exec(ExpandConstant('{app}\nssm.exe'), 'start AIHubJobScheduler', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('AIHubJobScheduler service started');

  // =========================================================================
  // Service 5: Vector API
  // =========================================================================
  ShellExec('', ExpandConstant('{app}\nssm.exe'),
    'install AIHubVectorAPI "' + ExpandConstant('{app}\wsgi_vector_api\wsgi_vector_api.exe') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if not UseSystemAccount then
  begin
    Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubVectorAPI ObjectName ' + LocalDomain + '\' + LocalUser + ' ' + LocalPwd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubVectorAPI Description "AI Hub vector API service"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  ConfigureServiceRecovery('AIHubVectorAPI');
  Exec(ExpandConstant('{app}\nssm.exe'), 'start AIHubVectorAPI', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('AIHubVectorAPI service started');

  // =========================================================================
  // Service 6: Agent API
  // =========================================================================
  ShellExec('', ExpandConstant('{app}\nssm.exe'),
    'install AIHubAgentAPI "' + ExpandConstant('{app}\wsgi_agent_api\wsgi_agent_api.exe') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if not UseSystemAccount then
  begin
    Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubAgentAPI ObjectName ' + LocalDomain + '\' + LocalUser + ' ' + LocalPwd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubAgentAPI Description "AI Hub agent API service"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  ConfigureServiceRecovery('AIHubAgentAPI');
  Exec(ExpandConstant('{app}\nssm.exe'), 'start AIHubAgentAPI', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('AIHubAgentAPI service started');

  // =========================================================================
  // Service 7: Knowledge API
  // =========================================================================
  ShellExec('', ExpandConstant('{app}\nssm.exe'),
    'install AIHubKnowledgeAPI "' + ExpandConstant('{app}\wsgi_knowledge_api\wsgi_knowledge_api.exe') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if not UseSystemAccount then
  begin
    Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubKnowledgeAPI ObjectName ' + LocalDomain + '\' + LocalUser + ' ' + LocalPwd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubKnowledgeAPI Description "AI Hub knowledge API service"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  ConfigureServiceRecovery('AIHubKnowledgeAPI');
  Exec(ExpandConstant('{app}\nssm.exe'), 'start AIHubKnowledgeAPI', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('AIHubKnowledgeAPI service started');

  // =========================================================================
  // Service 8: Executor service
  // =========================================================================
  ShellExec('', ExpandConstant('{app}\nssm.exe'),
    'install AIHubExecutorService "' + ExpandConstant('{app}\wsgi_executor_service\wsgi_executor_service.exe') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if not UseSystemAccount then
  begin
    Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubExecutorService ObjectName ' + LocalDomain + '\' + LocalUser + ' ' + LocalPwd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubExecutorService Description "AI Hub executor service"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  ConfigureServiceRecovery('AIHubExecutorService');
  Exec(ExpandConstant('{app}\nssm.exe'), 'start AIHubExecutorService', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('AIHubExecutorService service started');

  // =========================================================================
  // Service 9: MCP Gateway
  // =========================================================================
  ShellExec('', ExpandConstant('{app}\nssm.exe'),
    'install AIHubMCPGateway "' + ExpandConstant('{app}\mcp_gateway\app_mcp_gateway.exe') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if not UseSystemAccount then
  begin
    Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubMCPGateway ObjectName ' + LocalDomain + '\' + LocalUser + ' ' + LocalPwd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubMCPGateway Description "AI Hub MCP Gateway service"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  ConfigureServiceRecovery('AIHubMCPGateway');
  Exec(ExpandConstant('{app}\nssm.exe'), 'start AIHubMCPGateway', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('AIHubMCPGateway service started');

  // =========================================================================
  // Service 10: Cloud Storage Gateway
  // =========================================================================
  ShellExec('', ExpandConstant('{app}\nssm.exe'),
    'install AIHubCloudGateway "' + ExpandConstant('{app}\cloud_gateway\app_cloud_gateway.exe') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if not UseSystemAccount then
  begin
    Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubCloudGateway ObjectName ' + LocalDomain + '\' + LocalUser + ' ' + LocalPwd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubCloudGateway Description "AI Hub Cloud Storage Gateway service"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  ConfigureServiceRecovery('AIHubCloudGateway');
  Exec(ExpandConstant('{app}\nssm.exe'), 'start AIHubCloudGateway', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('AIHubCloudGateway service started');

  // =========================================================================
  // Service 11: Builder Service
  // =========================================================================
  ShellExec('', ExpandConstant('{app}\nssm.exe'),
    'install AIHubBuilderService "' + ExpandConstant('{app}\builder_service\builder_service.exe') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if not UseSystemAccount then
  begin
    Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubBuilderService ObjectName ' + LocalDomain + '\' + LocalUser + ' ' + LocalPwd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubBuilderService Description "AI Hub Builder Agent service"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  ConfigureServiceRecovery('AIHubBuilderService');
  Exec(ExpandConstant('{app}\nssm.exe'), 'start AIHubBuilderService', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('AIHubBuilderService service started');

  // =========================================================================
  // Service 12: Builder Data Service
  // =========================================================================
  ShellExec('', ExpandConstant('{app}\nssm.exe'),
    'install AIHubBuilderData "' + ExpandConstant('{app}\builder_data\builder_data.exe') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if not UseSystemAccount then
  begin
    Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubBuilderData ObjectName ' + LocalDomain + '\' + LocalUser + ' ' + LocalPwd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubBuilderData Description "AI Hub Data Pipeline Agent service"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  ConfigureServiceRecovery('AIHubBuilderData');
  Exec(ExpandConstant('{app}\nssm.exe'), 'start AIHubBuilderData', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('AIHubBuilderData service started');

  // =========================================================================
  // Service 13: Command Center Service
  // =========================================================================
  ShellExec('', ExpandConstant('{app}\nssm.exe'),
    'install AIHubCommandCenter "' + ExpandConstant('{app}\command_center_service\command_center_service.exe') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if not UseSystemAccount then
  begin
    Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubCommandCenter ObjectName ' + LocalDomain + '\' + LocalUser + ' ' + LocalPwd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;

  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubCommandCenter Description "AI Hub Command Center service"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  ConfigureServiceRecovery('AIHubCommandCenter');
  Exec(ExpandConstant('{app}\nssm.exe'), 'start AIHubCommandCenter', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('AIHubCommandCenter service started');

  Log('All 13 services installed and started successfully');

  // Get the configured port for browser launch
  GetConfiguredPort();
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ConfigFile: TFileStream;
  ConfigText: String;
  EnvConfigFile: String;
  AppRootVal: String;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    EnvConfigFile := ExpandConstant('{app}\.env');

    if IsUpgrade then
    begin
      // For upgrades, validate the existing API key
      WizardForm.StatusLabel.Caption := 'Validating existing license...';
      Log('Validating existing API key');

      if not ValidateApiKey(ExistingApiKey) then
      begin
        MsgBox('The API Key in your existing configuration is no longer valid.' + #13#10#13#10 +
               'Installation will now abort. Please contact support.', mbError, MB_OK);
        Log('ERROR: Existing API key validation failed');
        Abort;
      end;

      Log('Existing API key validated successfully');

      // --- Migrate API_KEY to secure registry storage ---
      // Write API_KEY to registry (may already be there from a previous v1.7+ install)
      WizardForm.StatusLabel.Caption := 'Securing credentials...';
      WriteApiKeyToRegistry(ExistingApiKey);
      // NOTE: Service account credentials (.env -> encrypted secrets) are migrated
      // automatically on first service startup by secure_config.load_secure_config()

      // =================================================================
      // Ensure required .env keys exist during upgrade
      // Each key is added only if missing; existing values are preserved
      // =================================================================
      WizardForm.StatusLabel.Caption := 'Updating configuration...';

      // --- APP_ROOT ---
      AppRootVal := ReadEnvFileFromPath(EnvConfigFile, 'APP_ROOT');
      if AppRootVal = '' then
      begin
        Log('APP_ROOT not found in .env during upgrade; adding it now...');
        if not EnsureEnvKeyExists(EnvConfigFile, 'APP_ROOT', ExpandConstant('{app}')) then
        begin
          MsgBox('Warning: Failed to write APP_ROOT to .env.' + #13#10 +
                 'You may need to add it manually: APP_ROOT=' + ExpandConstant('{app}'),
                 mbError, MB_OK);
        end;
      end
      else
        Log('APP_ROOT already present: ' + AppRootVal);

      // --- WORKFLOW_TRAINING_CAPTURE_ENABLED ---
      if not EnsureEnvKeyExists(EnvConfigFile, 'WORKFLOW_TRAINING_CAPTURE_ENABLED', 'false') then
      begin
        MsgBox('Warning: Failed to write WORKFLOW_TRAINING_CAPTURE_ENABLED to .env.' + #13#10 +
               'You may need to add it manually',
               mbError, MB_OK);
      end;

      // --- WORKFLOW_TRAINING_CAPTURE_PATH ---
      if not EnsureEnvKeyExists(EnvConfigFile, 'WORKFLOW_TRAINING_CAPTURE_PATH', './training_data/workflows') then
      begin
        MsgBox('Warning: Failed to write WORKFLOW_TRAINING_CAPTURE_PATH to .env.' + #13#10 +
               'You may need to add it manually',
               mbError, MB_OK);
      end;

      // --- USE_TWO_STAGE_ARCHITECTURE ---
      if not EnsureEnvKeyExists(EnvConfigFile, 'USE_TWO_STAGE_ARCHITECTURE', 'true') then
      begin
        MsgBox('Warning: Failed to write USE_TWO_STAGE_ARCHITECTURE to .env.' + #13#10 +
               'You may need to add it manually',
               mbError, MB_OK);
      end;

      // --- USE_WORKFLOW_EXECUTOR_SERVICE ---
      if not EnsureEnvKeyExists(EnvConfigFile, 'USE_WORKFLOW_EXECUTOR_SERVICE', 'true') then
      begin
        MsgBox('Warning: Failed to write USE_WORKFLOW_EXECUTOR_SERVICE to .env.' + #13#10 +
               'You may need to add it manually',
               mbError, MB_OK);
      end;

      // --- KNOWLEDGE_SERVER_THREADS ---
      if not EnsureEnvKeyExists(EnvConfigFile, 'KNOWLEDGE_SERVER_THREADS', '2') then
      begin
        MsgBox('Warning: Failed to write KNOWLEDGE_SERVER_THREADS to .env.' + #13#10 +
               'You may need to add it manually',
               mbError, MB_OK);
      end;

      // --- EXECUTOR_SERVICE_THREADS ---
      if not EnsureEnvKeyExists(EnvConfigFile, 'EXECUTOR_SERVICE_THREADS', '4') then
      begin
        MsgBox('Warning: Failed to write EXECUTOR_SERVICE_THREADS to .env.' + #13#10 +
               'You may need to add it manually',
               mbError, MB_OK);
      end;

      // --- EMAIL_PROVIDER (v1.8) ---
      if not EnsureEnvKeyExists(EnvConfigFile, 'EMAIL_PROVIDER', 'azure') then
      begin
        MsgBox('Warning: Failed to write EMAIL_PROVIDER to .env.' + #13#10 +
               'You may need to add it manually',
               mbError, MB_OK);
      end;

    end
    else
    begin
      // For new installations, validate the entered API key
      WizardForm.StatusLabel.Caption := 'Validating API key...';
      Log('Validating new API key');

      if not ValidateApiKey(ApiKeyEdit.Text) then
      begin
        MsgBox('Invalid API Key. Installation will now abort.', mbError, MB_OK);
        Log('ERROR: New API key validation failed');
        Abort;
      end;

      // Create the configuration file for new installations
      // NOTE: Sensitive values (API_KEY, passwords) are stored securely:
      //   API_KEY -> Windows Registry (HKLM\Software\AI Hub\Config)
      //   Service/WinRM credentials -> Encrypted LocalSecretsManager
      //   .env only contains non-sensitive configuration
      WizardForm.StatusLabel.Caption := 'Writing configuration...';
      Log('Creating new .env configuration file (non-sensitive values only)');

      // Write API_KEY to registry
      WriteApiKeyToRegistry(ApiKeyEdit.Text);

      try
        // .env gets non-sensitive config plus service account creds (temporary).
        // On first service startup, secure_config.load_secure_config() will
        // auto-migrate the credentials to encrypted LocalSecretsManager.
        ConfigText := #13#10 +
          'HOST_PORT=' + PortEdit.Text + #13#10 +
          'APP_ROOT=' + ExpandConstant('{app}') + #13#10 +
          'USE_TWO_STAGE_ARCHITECTURE=true' + #13#10 +
          'USE_WORKFLOW_EXECUTOR_SERVICE=true' + #13#10 +
          'KNOWLEDGE_SERVER_THREADS=2' + #13#10 +
          'EXECUTOR_SERVICE_THREADS=4' + #13#10 +
          'WORKFLOW_TRAINING_CAPTURE_ENABLED=false' + #13#10 +
          'EMAIL_PROVIDER=azure' + #13#10;

        // Only write service account credentials if not using Local System
        if not ReadOnlyCheckBox.Checked then
        begin
          ConfigText := ConfigText +
            'WINTASK_USER=' + LocalUserEdit.Text + #13#10 +
            'WINTASK_PWD=' + LocalPwdEdit.Text + #13#10 +
            'LOCAL_DOMAIN=' + LocalDomainEdit.Text + #13#10 +
            'WINRM_USER=' + LocalUserEdit.Text + #13#10 +
            'WINRM_PWD=' + LocalPwdEdit.Text + #13#10 +
            'WINRM_DOMAIN=' + LocalDomainEdit.Text + #13#10;
          Log('Service account credentials written to .env (will be migrated to encrypted store on first startup)');
        end;

        SaveStringToFile(EnvConfigFile, ConfigText, True);
        Log('Configuration file created successfully');
      finally
        ConfigFile := nil;
      end;
    end;

    // Install services (works for both new installs and upgrades)
    InstallServices();

    // Open browser after waiting for services to start
    // Use background process so installer can close immediately
    if not WizardSilent then
    begin
      if IsUpgrade then
      begin
        Log('Upgrade complete - scheduling browser open after services start');
        Exec('cmd.exe', '/c timeout /t 10 /nobreak >nul && start http://localhost:' + ConfiguredPort,
             '', SW_HIDE, ewNoWait, ResultCode);
      end
      else
      begin
        Log('Fresh install - scheduling browser open after services start on port ' + ConfiguredPort);
        Exec('cmd.exe', '/c timeout /t 20 /nobreak >nul && start http://localhost:' + ConfiguredPort,
             '', SW_HIDE, ewNoWait, ResultCode);
      end;
    end
    else
      Log('Silent install/upgrade - skipping browser launch');

    // Create browser shortcuts programmatically using the configured port
    // (cannot use [Icons] section because it doesn't support scripted variables)
    Log('Creating browser shortcuts with port: ' + ConfiguredPort);

    // Start Menu shortcut - "Open AI Hub in Browser"
    CreateShellLink(
      ExpandConstant('{group}\Open AI Hub in Browser.lnk'),
      'Open AI Hub in your web browser',
      'http://localhost:' + ConfiguredPort,
      '', '', '', 0, SW_SHOWNORMAL);

    // Desktop shortcut (only if user selected the desktopicon task)
    if WizardIsTaskSelected('desktopicon') then
    begin
      CreateShellLink(
        ExpandConstant('{commondesktop}\AI Hub.lnk'),
        'Open AI Hub in your browser',
        'http://localhost:' + ConfiguredPort,
        '', ExpandConstant('{app}\static\icons\aihub.ico'), '', 0, SW_SHOWNORMAL);
    end;

    Log('Browser shortcuts created successfully');
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
var
  FinishMsg: String;
  DisplayPort: String;
begin
  // Customize the finish page with clear messaging about web-based access
  if CurPageID = wpFinished then
  begin
    // Determine which port to display
    if IsUpgrade then
      DisplayPort := ConfiguredPort
    else
      DisplayPort := PortEdit.Text;

    // Fallback to default if empty
    if DisplayPort = '' then
      DisplayPort := '5001';

    FinishMsg := 'AI Hub is a web-based application that runs locally on your computer.' + #13#10 + #13#10 +
                 'Services are now starting. ' +
                 'Your browser will open automatically when ready.' + #13#10 + #13#10;

    // Add desktop shortcut info if the task was selected
    if WizardIsTaskSelected('desktopicon') then
    begin
      FinishMsg := FinishMsg +
                   'A desktop shortcut "AI Hub" has been created for easy access.' + #13#10 + #13#10;
    end;

    FinishMsg := FinishMsg +
                 'You can also access AI Hub anytime by opening your browser to:' + #13#10 +
                 'http://localhost:' + DisplayPort;

    WizardForm.FinishedLabel.Caption := FinishMsg;
  end;
end;

[UninstallRun]
Filename: "{app}\nssm.exe"; Parameters: "stop AIHub"; Flags: runhidden waituntilterminated
Filename: "{app}\nssm.exe"; Parameters: "remove AIHub confirm"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "stop AIHubDocAPI"; Flags: runhidden waituntilterminated
Filename: "{app}\nssm.exe"; Parameters: "remove AIHubDocAPI confirm"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "stop AIHubDocQueue"; Flags: runhidden waituntilterminated
Filename: "{app}\nssm.exe"; Parameters: "remove AIHubDocQueue confirm"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "stop AIHubJobScheduler"; Flags: runhidden waituntilterminated
Filename: "{app}\nssm.exe"; Parameters: "remove AIHubJobScheduler confirm"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "stop AIHubVectorAPI"; Flags: runhidden waituntilterminated
Filename: "{app}\nssm.exe"; Parameters: "remove AIHubVectorAPI confirm"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "stop AIHubAgentAPI"; Flags: runhidden waituntilterminated
Filename: "{app}\nssm.exe"; Parameters: "remove AIHubAgentAPI confirm"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "stop AIHubKnowledgeAPI"; Flags: runhidden waituntilterminated
Filename: "{app}\nssm.exe"; Parameters: "remove AIHubKnowledgeAPI confirm"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "stop AIHubExecutorService"; Flags: runhidden waituntilterminated
Filename: "{app}\nssm.exe"; Parameters: "remove AIHubExecutorService confirm"; Flags: runhidden
; MCP Gateway, Cloud Gateway, Builder Service, Builder Data, Command Center
Filename: "{app}\nssm.exe"; Parameters: "stop AIHubMCPGateway"; Flags: runhidden waituntilterminated
Filename: "{app}\nssm.exe"; Parameters: "remove AIHubMCPGateway confirm"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "stop AIHubCloudGateway"; Flags: runhidden waituntilterminated
Filename: "{app}\nssm.exe"; Parameters: "remove AIHubCloudGateway confirm"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "stop AIHubBuilderService"; Flags: runhidden waituntilterminated
Filename: "{app}\nssm.exe"; Parameters: "remove AIHubBuilderService confirm"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "stop AIHubBuilderData"; Flags: runhidden waituntilterminated
Filename: "{app}\nssm.exe"; Parameters: "remove AIHubBuilderData confirm"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "stop AIHubCommandCenter"; Flags: runhidden waituntilterminated
Filename: "{app}\nssm.exe"; Parameters: "remove AIHubCommandCenter confirm"; Flags: runhidden

[UninstallRegistry]
Root: HKLM; Subkey: "Software\AI Hub\Config"; Flags: deletekey
Root: HKLM; Subkey: "Software\AI Hub"; Flags: dontcreatekey uninsdeletekeyifempty

[UninstallDelete]
Type: files; Name: "{app}\*.exe"
Type: files; Name: "{app}\*.yaml"
Type: files; Name: "{app}\cache\*.*"
Type: files; Name: "{app}\logs\*.*"
//Type: files; Name: "{app}\tools\*.*"       // Keep client tools
Type: files; Name: "{app}\flask_session\*.*"
Type: files; Name: "{app}\assets\prompt_templates\*.*"
Type: files; Name: "{app}\assets\*.*"
Type: files; Name: "{app}\app\assets\prompt_templates\*.*"
Type: files; Name: "{app}\app\assets\*.*"
Type: files; Name: "{app}\exports\charts\*.*"
Type: files; Name: "{app}\exports\*.*"
Type: files; Name: "{app}\tmp\*.*"
Type: files; Name: "{app}\temp\*.*"
Type: files; Name: "{app}\uploads\*.*"
Type: files; Name: "{app}\schemas\*.*"
Type: files; Name: "{app}\*.dat"
Type: files; Name: "{app}\static\*.*"
Type: files; Name: "{app}\updates\*.*"
Type: files; Name: "{app}\agent_environments\python-bundle\*.*"
Type: files; Name: "{app}\agent_environments\python-bundle-requirements\*.*"
Type: dirifempty; Name: "{app}\cache"
Type: dirifempty; Name: "{app}\logs"
Type: dirifempty; Name: "{app}\tools"
Type: dirifempty; Name: "{app}\flask_session"
Type: dirifempty; Name: "{app}\assets\prompt_templates"
Type: dirifempty; Name: "{app}\assets"
Type: dirifempty; Name: "{app}\app\assets\prompt_templates"
Type: dirifempty; Name: "{app}\app\assets"
Type: dirifempty; Name: "{app}\exports\charts"
Type: dirifempty; Name: "{app}\exports"
Type: dirifempty; Name: "{app}\tmp"
Type: dirifempty; Name: "{app}\temp"
Type: dirifempty; Name: "{app}\uploads"
Type: dirifempty; Name: "{app}\schemas"
Type: dirifempty; Name: "{app}\static"
Type: dirifempty; Name: "{app}\updates"
Type: dirifempty; Name: "{app}\agent_environments\python-bundle"
Type: dirifempty; Name: "{app}\agent_environments\python-bundle-requirements"
Type: dirifempty; Name: "{app}"

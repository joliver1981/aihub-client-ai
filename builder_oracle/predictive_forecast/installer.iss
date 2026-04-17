; PredictiveForecast - Inno Setup Installer Script
; Build: iscc installer.iss
; Requires: Inno Setup 6.x (https://jrsoftware.org/isinfo.php)

#define MyAppName "PredictiveForecast"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "AI Hub"
#define MyAppURL "https://localhost:5005"
#define MyAppExeName "predictive_forecast.exe"
#define MyAppPort "5005"

[Setup]
AppId={{A7F3E8B2-4C91-4D6E-8A5B-1F2E3D4C5B6A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=installer_output
OutputBaseFilename=PredictiveForecast_Setup_{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitModeOnly=x64compatible
MinVersion=10.0
PrivilegesRequired=admin
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupservice"; Description: "Run as background service on Windows startup"; GroupDescription: "Service Options:"

[Files]
; Main application (from PyInstaller onedir output)
Source: "dist\predictive_forecast\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Create writable data directories
Source: "models\*"; DestDir: "{app}\models"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist onlyifdoesntexist
Source: "uploads\*"; DestDir: "{app}\uploads"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist onlyifdoesntexist

[Dirs]
Name: "{app}\models"; Permissions: users-modify
Name: "{app}\uploads"; Permissions: users-modify
Name: "{app}\logs"; Permissions: users-modify

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} Web UI"; Filename: "http://localhost:{#MyAppPort}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Open browser after install
Filename: "http://localhost:{#MyAppPort}"; Description: "Open PredictiveForecast in browser"; Flags: postinstall shellexec nowait skipifsilent unchecked

; Start the service
Filename: "{app}\{#MyAppExeName}"; Description: "Start PredictiveForecast service"; Flags: postinstall nowait skipifsilent runhidden

[UninstallRun]
; Stop the service before uninstall
Filename: "taskkill"; Parameters: "/f /im {#MyAppExeName}"; Flags: runhidden

[Registry]
; Store port config in registry
Root: HKLM; Subkey: "Software\{#MyAppPublisher}\{#MyAppName}"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\{#MyAppPublisher}\{#MyAppName}"; ValueType: string; ValueName: "Port"; ValueData: "{#MyAppPort}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\{#MyAppPublisher}\{#MyAppName}"; ValueType: string; ValueName: "Version"; ValueData: "{#MyAppVersion}"; Flags: uninsdeletekey

; Auto-start on login (if task selected)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startupservice

[Code]
// Check if port is available before install
function IsPortAvailable(Port: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  // Use netstat to check if port is in use
  if Exec('cmd.exe', '/c netstat -an | findstr :' + Port + ' | findstr LISTENING', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if ResultCode = 0 then
      Result := False;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsPortAvailable('{#MyAppPort}') then
  begin
    if MsgBox('Port {#MyAppPort} appears to be in use. ' +
              'PredictiveForecast may not start correctly. ' +
              'Continue with installation?', mbConfirmation, MB_YESNO) = IDNO then
    begin
      Result := False;
    end;
  end;
end;

// Stop existing instance before upgrade
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
  begin
    Exec('taskkill', '/f /im {#MyAppExeName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

// Create environment file with default settings
procedure CurStepChanged2(CurStep: TSetupStep);
var
  EnvFile: String;
begin
  if CurStep = ssPostInstall then
  begin
    EnvFile := ExpandConstant('{app}\.env');
    if not FileExists(EnvFile) then
    begin
      SaveStringToFile(EnvFile,
        'FC_HOST=0.0.0.0' + #13#10 +
        'FC_PORT={#MyAppPort}' + #13#10 +
        'FC_THREADS=4' + #13#10 +
        'FC_DEBUG=false' + #13#10 +
        'FC_MAX_UPLOAD_MB=500' + #13#10,
        False);
    end;
  end;
end;

#define AppVersion GetEnv("WGB_APP_VERSION")
#define SourceDir GetEnv("WGB_SOURCE_DIR")
#define OutputDir GetEnv("WGB_OUTPUT_DIR")
#define RequestedOutputBaseName GetEnv("WGB_OUTPUT_BASENAME")
#define AppName "WG Map Backporter Studio"
#define AppExeName "WGMapBackporterStudio.exe"
#define Publisher "RhysHopkins04"

#if AppVersion == ""
  #error "WGB_APP_VERSION environment variable is required"
#endif
#if SourceDir == ""
  #error "WGB_SOURCE_DIR environment variable is required"
#endif
#if OutputDir == ""
  #error "WGB_OUTPUT_DIR environment variable is required"
#endif

#if RequestedOutputBaseName == ""
  #define OutputBaseName "WGMapBackporterStudio-" + AppVersion + "-Windows-x64-Setup"
#else
  #define OutputBaseName RequestedOutputBaseName
#endif

[Setup]
AppId={{7FE93A49-9F63-4F28-9895-B7045085B57F}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#Publisher}
DefaultDirName={localappdata}\Programs\WG Map Backporter Studio
DefaultGroupName=WG Map Backporter Studio
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseName}
SetupIconFile=..\..\resources\app_icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
SignedUninstaller=no
VersionInfoCompany={#Publisher}
VersionInfoDescription={#AppName} installer
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoVersion={#AppVersion}.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\WG Map Backporter Studio"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\WG Map Backporter Studio"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch WG Map Backporter Studio"; Flags: nowait postinstall skipifsilent

#!/bin/bash
# Gestore monouso per moodlemobile://token=... : salva il token con moodle.py login e poi si cancella.
set -e
APP="$HOME/Applications/MoodleSapienzaLogin.app"
SCRIPT="$HOME/moodle-sapienza/scripts/moodle.py"
LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
mkdir -p "$HOME/Applications" "$HOME/.config/moodle-sapienza"
rm -rf "$APP"
TMP=$(mktemp -d)
cat > "$TMP/h.applescript" <<EOF
on open location theURL
	if theURL does not start with "moodlemobile://token=" then return
	set cmd to "/usr/bin/python3 $SCRIPT login --token " & quoted form of theURL & " > $HOME/.config/moodle-sapienza/last-login.log 2>&1"
	try
		do shell script cmd
		display notification "Token salvato. Gestore rimosso." with title "Moodle Sapienza"
	on error
		display notification "Salvataggio fallito, vedi last-login.log" with title "Moodle Sapienza"
	end try
	do shell script "(sleep 3; $LSREG -u $APP; rm -rf $APP) >/dev/null 2>&1 &"
end open location
EOF
osacompile -o "$APP" "$TMP/h.applescript"
P="$APP/Contents/Info.plist"
PB=/usr/libexec/PlistBuddy
$PB -c "Add :CFBundleIdentifier string it.local.moodlesapienzalogin" "$P" 2>/dev/null || $PB -c "Set :CFBundleIdentifier it.local.moodlesapienzalogin" "$P"
$PB -c "Add :LSUIElement bool true" "$P" 2>/dev/null || true
$PB -c "Add :CFBundleURLTypes array" "$P"
$PB -c "Add :CFBundleURLTypes:0 dict" "$P"
$PB -c "Add :CFBundleURLTypes:0:CFBundleURLName string Moodle Sapienza login" "$P"
$PB -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes array" "$P"
$PB -c "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string moodlemobile" "$P"
codesign --force --deep --sign - "$APP" 2>/dev/null || true
"$LSREG" -f "$APP"
rm -rf "$TMP"
echo "Gestore installato. Ora apri il link di login in Chrome; al redirect scegli di aprire 'MoodleSapienzaLogin'."

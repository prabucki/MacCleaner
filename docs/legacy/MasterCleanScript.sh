#!/usr/bin/env bash

# Ask for the administrator password upfront
sudo -v

# Keep-alive sudo until the script has finished
while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &

# Remount system partition as read/write (only till next reboot)
sudo mount -uw /

###############################################################################
# fwartner's mac-cleanup (https://github.com/fwartner/mac-cleanup)            #
###############################################################################

# Run it
mac-cleanup -f

###############################################################################
# Updating                                                                    #
###############################################################################

# ALREADY DONE BY FWARTNER'S SCRIPT
# brew update
# brew upgrade
brew cleanup -s
rm -rf $(brew --cache)

# Update all globally installed npm binaries
npm update -g

# Upgrade oh-my-zsh
env ZSH=$ZSH /bin/sh $ZSH/tools/upgrade.sh

# Upgrades all outdated casks
# (--greedy parameter updates more apps, but may cause apps to reinstall on each re-run)
brew upgrade --cask --greedy

###############################################################################
# Cleaning                                                                    #
###############################################################################

sudo purge

gem cleanup

# Delete old Xcode Simulators
xcrun simctl delete unavailable

# clean user cache files
sudo rm -rf ~/Library/Caches/*

# clean system cache files
sudo rm -rf /Library/Caches/*

#clean user log files
sudo rm -rf ~/Library/logs/*

#clean system log files
sudo rm -rf /Library/logs/*
sudo rm -rf /var/log/*

# Delete All System Logs in OS X
sudo rm -rf /private/var/log/*

#clean per-user caches and temporary files - rearranges LaunchPad icons :(
# sudo rm -rf /private/var/folders/*

sudo rm -rf /private/var/folders/1v/x2bn002s3cz0c5jc0g7sy9bh0000gn/C/*

sudo rm -rf ~/.cache/*
sudo rm -rf ~/Library/Caches/*
sudo rm -rf ~/Library/Application\ Support/coreMLCache/*

# clear font caches
atsutil databases -removeUser && \
atsutil server -shutdown && \
atsutil server -ping

# rebuild XPC caches
# (doesn't seem to be working anymore)
# sudo /usr/libexec/xpchelper --rebuild-cache

# rebuild CoreDuet
sudo rm -fr /var/db/coreduet/*

# rebuild Launch Services
sudo /System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister -kill -r -seed -domain local -domain system -domain user

# Flush DNS cache and restart mdns
sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder

# clear BootCache
sudo rm -f /private/var/db/BootCache.playlist

# update Dyld cache
# (seems to be deprecated)
# sudo update_dyld_shared_cache -root / -force

# rebuild Kernel extension caches
# (seems to be deprecated)
sudo touch /System/Library/Extensions && sudo kextcache -u /

# run system scripts
sudo periodic daily weekly monthly

# This seems to be breaking my Macbook
# sudo find / \( -name *.lproj -and \! \( -name English.lproj -or -name en.lproj -or -name en_AU.lproj -or -name en_CA.lproj -or -name Polish.lproj -or -name pl.lproj -or -name en_GB.lproj \) \) -exec rm -rf {} \;

# clean cask MSOffice installers
sudo rm -rf /opt/homebrew/Caskroom/microsoft-excel/1*/*
sudo rm -rf /opt/homebrew/Caskroom/microsoft-powerpoint/1*/*
sudo rm -rf /opt/homebrew/Caskroom/microsoft-word/1*/*

# remove Premiere Pro tutorial (~166mb)
sudo rm -rf /Users/Shared/Adobe/Premiere\ Pro/1*/*

# remove unused XCode platforms
# (breaks XCode)
# sudo rm -rf /Applications/Xcode.app/Contents/Developer/Platforms/AppleTVOS.platform
# sudo rm -rf /Applications/Xcode.app/Contents/Developer/Platforms/AppleTVSimulator.platform
# sudo rm -rf /Applications/Xcode.app/Contents/Developer/Platforms/WatchOS.platform
# sudo rm -rf /Applications/Xcode.app/Contents/Developer/Platforms/WatchSimulator.platform

sudo rm -rf /Applications/Xcode.app/Contents/Developer/Platforms/AppleTVOS.platform/Library/Developer/CoreSimulator/Profiles/*
sudo rm -rf /Applications/Xcode.app/Contents/Developer/Platforms/AppleTVOS.platform/DeviceSupport/*
sudo rm -rf /Applications/Xcode.app/Contents/Developer/Platforms/WatchOS.platform/Library/Developer/CoreSimulator/Profiles/*
sudo rm -rf /Applications/Xcode.app/Contents/Developer/Platforms/WatchOS.platform/DeviceSupport/*

# Delete Xcode Archived Applications
sudo rm -rf ~/Library/Developer/Xcode/Archives/*/

# Delete Xcode Devired Data
sudo rm -rf ~/Library/Developer/Xcode/DerivedData/*/

# Delete Xcode Apple cached files
sudo rm -rf ~/Library/Developer/CoreSimulator/Caches/dyld/*/*/

# Delegete Xcode cache on com.apple.DeveloperTools
sudo rm -rf /private/var/folders/dk/*/C/com.apple.DeveloperTools/*/


# remove eunused text-to-speech voices (~669mb)
sudo rm -rf /System/Library/Speech/Voices/Daniel.SpeechVoice
sudo rm -rf /System/Library/Speech/Voices/Zosia.SpeechVoice

# remove iOS Simulator cache
sudo rm -rf ~/Library/Developer/CoreSimulator/Caches

# cleanup nuget
nuget locals all -clear

# remove BetterTouchTool's clipboard history
sudo rm -rf ~/Library/Application\ Support/BetterTouchTool/.BTTClipboardManager_SUPPORT/_EXTERNAL_DATA/*

# clean nvm cache
sudo rm -rf ~/.nvm/.cache/*

# remove gradle
sudo rm -rf ~/.gradle/*

# Clean npm cache
npm cache clean --force

# Clean npx
sudo rm -rf ~/.npm/_npx

# Clean Ferdi cache
sudo rm -rf ~/Library/Application\ Support/Ferdi/Partitions/service-2e9cf35b-ef42-4093-b7f2-d33d203c6b78/Cache
sudo rm -rf ~/Library/Application\ Support/Ferdi/Partitions/service-2e9cf35b-ef42-4093-b7f2-d33d203c6b78/Code\ Cache
sudo rm -rf ~/Library/Application\ Support/Ferdi/Partitions/service-4e4dc2af-938e-439e-80da-43c0c9aa795e/Cache
sudo rm -rf ~/Library/Application\ Support/Ferdi/Partitions/service-4e4dc2af-938e-439e-80da-43c0c9aa795e/Code\ Cache
sudo rm -rf ~/Library/Application\ Support/Ferdi/Partitions/service-8900948b-3880-4b2c-9f61-0a9185e4167f/Cache
sudo rm -rf ~/Library/Application\ Support/Ferdi/Partitions/service-8900948b-3880-4b2c-9f61-0a9185e4167f/Code\ Cache
sudo rm -rf ~/Library/Application\ Support/Ferdi/Partitions/service-d0dadf98-b115-486f-85c3-2d93f6b0db48/Cache
sudo rm -rf ~/Library/Application\ Support/Ferdi/Partitions/service-d0dadf98-b115-486f-85c3-2d93f6b0db48/Code\ Cache
sudo rm -rf ~/Library/Application\ Support/Ferdi/Partitions/service-d0dadf98-b115-486f-85c3-2d93f6b0db48/Service\ Worker/CacheStorage

# remove Adobe bloatware
# ENABLE ALL THESE ONCE WE MOVE BACK TO PIRATED ADOBE
# sudo rm -rf /Applications/CAI
# sudo rm -rf /Applications/Utilities/Adobe\ Creative\ Cloud
# sudo rm -rf /Applications/Utilities/Adobe\ Creative\ Cloud\ Experience
# sudo rm -rf /Applications/Utilities/Adobe\ Acrobat\ DC/Adobe\ Distiller.app
# sudo rm -rf /Applications/Utilities/Adobe\ Installers
# sudo rm -rf /Applications/Utilities/Adobe\ Sync/
# sudo rm -rf /Applications/Adobe\ Creative\ Cloud
# sudo rm -rf ~/Creative\ Cloud\ Files
# sudo rm -rf /Library/Application\ Support/Adobe/Creative\ Cloud\ Libraries
# sudo rm -rf ~/Library/Caches/Adobe
# sudo rm -rf /opt/homebrew/Caskroom/adobe-creative-cloud
# sudo rm -rf /Library/Application\ Support/Adobe/Plug\-Ins
# sudo rm -rf ~/Library/Application\ Support/Adobe/Common/PTX
# sudo rm -rf ~/Library/Application\ Support/Adobe/com\.adobe\.ARMDCHelper
# sudo rm -rf /Library/Application\ Support/Adobe/CameraRaw
# sudo rm -rf /Library/Application\ Support/Adobe/Adobe\ Desktop\ Common/CEF
# sudo rm -rf /Library/Application\ Support/Adobe/Adobe\ Desktop\ Common/AppsPanel
# sudo rm -rf /Library/Application\ Support/Adobe/Adobe\ Desktop\ Common/RemoteComponents
# sudo rm -rf /Library/Application\ Support/Adobe/Adobe\ Desktop\ Common/HDBox
# sudo rm -rf /Library/Application\ Support/Adobe/Adobe\ Desktop\ Common/LCC
# sudo rm -rf /Library/Application\ Support/Adobe/CEP
# sudo rm -rf /Library/Application\ Support/Adobe/UXP


# Remove annoying Apple Illustrator scripts acting as apps
sudo rm -rf /Applications/Adobe\ Illustrator\ 2025/Scripting.localized/Sample\ Scripts.localized/AppleScript.localized/Web\ Gallery.localized
sudo rm -rf /Applications/Adobe\ Illustrator\ 2025/Scripting.localized/Sample\ Scripts.localized/AppleScript.localized/Calendar.localized
sudo rm -rf /Applications/Adobe\ Illustrator\ 2025/Scripting.localized/Sample\ Scripts.localized/AppleScript.localized/Contact\ Sheet\ Demo.localized

# Remove lauchagents/daemons
# ENABLE ALL THESE ONCE WE MOVE BACK TO PIRATED ADOBE
# sudo rm -rf /Library/LaunchAgents/com.adobe.AdobeCreativeCloud.plist
# sudo rm -rf /Library/LaunchAgents/com.adobe.ARMDCHelper.cc24aef4a1b90ed56a725c38014c95072f92651fb65e1bf9c8e43c37a23d420d.plist
# sudo rm -rf /Library/LaunchAgents/com.adobe.ccxprocess.plist
# sudo rm -rf /Library/LaunchDaemons/com.adobe.acc.installer.v2.plist
# sudo rm -rf /Library/LaunchDaemons/com.adobe.ARMDC.Communicator.plist
# sudo rm -rf /Library/LaunchDaemons/com.adobe.ARMDC.SMJobBlessHelper.plist

# ENABLE ALL THESE ONCE WE MOVE BACK TO PIRATED ADOBE
# sudo rm -rf /Users/Shared/Adobe
# sudo rm -rf /Users/Shared/AdobeGCInfo

# ADOBE ESSENTIAL FILES:

# Removing breaks Photoshop startup
# /Applications/Utilities/Adobe\ Application\ Manager/pim.db
# /Library/Application\ Support/Adobe/Adobe\ Desktop\ Common
# /Library/Application Support/Adobe/Adobe Desktop Common/IPCBox/AdobeIPCBroker.app

# Removing breaks Adobe Acrobat Save As window
# /Library/Application\ Support/Adobe/Acrobat

# sudo rm -rf /Library/Application\ Support/Apple/Photos/Print\ Products

# Disabled due to lack of system access since Big Sur
#finish removing system wallpapers here (~)
# sudo rm -rf /System/Library/Desktop\ Pictures/Big\ Sur.heic
# sudo rm -rf /System/Library/Desktop\ Pictures/Catalina.heic
# sudo rm -rf /System/Library/Desktop\ Pictures/The\ Lake.heic
# sudo rm -rf /System/Library/Desktop\ Pictures/Solar\ Gradients.heic
# sudo rm -rf /System/Library/Desktop\ Pictures/The\ Cliffs.heic
# sudo rm -rf /System/Library/Desktop\ Pictures/The\ Dessert.heic
# sudo rm -rf /System/Library/Desktop\ Pictures/The\ Beach.heic
# sudo rm -rf /System/Library/Desktop\ Pictures/Dome.heic
# sudo rm -rf /System/Library/Desktop\ Pictures/Valley.heic

# Kill MS Teams if opened
pkill -x Teams

# Clean MS Teams cache
sudo rm -rf ~/Library/Application\ Support/Microsoft/Teams/

sudo rm -rf ~/Library/Application\ Support/Caches/*
sudo rm -rf ~/Library/Caches/*

# Clean Battle.net cache
sudo rm -rf /Users/Shared/Blizzard/Battle.net/Cache

# Clean old Cleanshot screenshots/videos
sudo rm -rf ~/Library/Application\ Support/Cleanshot/Media/*

# Clean Transmit logs
sudo rm -rf ~/Library/Application\ Support/Transmit/Logs/*

# Remove Duplicate File Finder logs
sudo rm -rf ~/Library/Application\ Support/com.nektony.Duplicate-File-Finder-SIII/Removed/*.log

# Remove cask installer packages (may break upgrade functionality?)
find "$(brew --prefix)/Caskroom" -type f -name '*.pkg' -delete

###############################################################################
# Remove old Android NDK / Platform except latest                             #
###############################################################################

clean_keep_latest() {
  TARGET_DIR="$1"

  echo "Cleaning directory: $TARGET_DIR"

  cd "$TARGET_DIR" || exit 1

  # Detect folders and extract version numbers
  latest=$(
    ls -1d */ \
    | sed 's:/$::' \
    | sed 's/android-//' \
    | sort -V \
    | tail -n 1
  )

  # Rebuild folder name if the prefix was stripped (android- case)
  # Check if folders start with "android-"
  if ls -1d android-* >/dev/null 2>&1; then
    latest_folder="android-$latest"
  else
    latest_folder="$latest"
  fi

  echo "👉 Keeping: $latest_folder"
  echo "🗑️  Deleting the rest…"

  for d in */; do
    d="${d%/}"
    if [ "$d" != "$latest_folder" ]; then
      echo "Deleting: $d"
      rm -rf -- "$d"
    fi
  done
}

# Example usage:
clean_keep_latest "$HOME/Library/Android/sdk/ndk"
clean_keep_latest "$HOME/Library/Android/sdk/platforms"

###############################################################################
# Other                                                                       #
###############################################################################

# Reset XtraFinder trial
plutil -remove FirstRunDate ~/Library/Preferences/com.apple.finder.plist

# Cleaning application caches
for x in $(ls ~/Library/Containers/)
do
    echo "cleaning ~/Library/Containers/$x/Data/Library/Caches/"
    rm -rf ~/Library/Containers/$x/Data/Library/Caches/*
    echo "done cleaning ~/Library/Containers/$x/Data/Library/Caches"
done

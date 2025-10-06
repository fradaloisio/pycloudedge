"""
CloudEdge IoT Parameters
================================

Comprehensive IoT parameter codes for device configuration and control.

Author: Francesco D'Aloisio  
Date: September 16, 2025
"""

IOT_PARAMETERS = {
    # User and Device Identity
    "1": {
        "name": "USER_ID",
        "description": "User identifier"
    },
    "3": {
        "name": "DEVICE_KEY", 
        "description": "Device key"
    },
    "10": {
        "name": "CLOUD_VIDEO_EXPIRY",
        "description": "Cloud video service expiration date"
    },
    "14": {
        "name": "REFRESH_FACE_LIST",
        "description": "Refresh face list"
    },
    "21": {
        "name": "SAFE_PASSWORD",
        "description": "Safe password"
    },
    
    # Device Information
    "8": {
        "name": "DEVICE_STATUS",
        "description": "Device status flag"
    },
    "9": {
        "name": "DEVICE_MODE",
        "description": "Device operation mode"
    },
    "15": {
        "name": "DEVICE_FLAGS",
        "description": "Device configuration flags"
    },
    "18": {
        "name": "DEVICE_STATE",
        "description": "Device state parameter"
    },
    "50": {
        "name": "SERIAL_NUMBER",
        "description": "Serial number"
    },
    "51": {
        "name": "FIRMWARE_CODE", 
        "description": "Firmware code"
    },
    "52": {
        "name": "FIRMWARE_VERSION",
        "description": "Firmware version"
    },
    "53": {
        "name": "CLOUD_UPLOAD_ENABLE",
        "description": "Cloud upload enable"
    },
    "54": {
        "name": "TIME_ZONE",
        "description": "Time zone"
    },
    "55": {
        "name": "CAPABILITIES",
        "description": "Capabilities"
    },
    "56": {
        "name": "MAC_ADDRESS",
        "description": "MAC address"
    },
    "57": {
        "name": "DNS_SERVERS",
        "description": "DNS servers"
    },
    "58": {
        "name": "SUBNET_MASK",
        "description": "Subnet mask"
    },
    "59": {
        "name": "GATEWAY",
        "description": "Gateway/Router"
    },
    "60": {
        "name": "LAST_CHECK_TIME",
        "description": "Last check time"
    },
    "61": {
        "name": "LICENSE_ID",
        "description": "License identifier"
    },
    "62": {
        "name": "SUPPORT_VERSION",
        "description": "Support version"
    },
    "63": {
        "name": "DEVICE_MODEL",
        "description": "Device model"
    },
    "64": {
        "name": "PLATFORM_CODE",
        "description": "Platform code"
    },
    "65": {
        "name": "DEVICE_ONLINE_TIME",
        "description": "Device connection time"
    },
    "66": {
        "name": "TP",
        "description": "TP parameter"
    },
    "67": {
        "name": "NVR_NEUTRAL_CHANNEL_CAPS",
        "description": "NVR neutral channel capabilities"
    },
    "68": {
        "name": "NVR_NEUTRAL_QR_CODE_KEY",
        "description": "NVR neutral QR code key"
    },
    "69": {
        "name": "MEDIA_QUANTITY",
        "description": "Media quantity"
    },
    "70": {
        "name": "AFTER_SALE",
        "description": "After sale"
    },
    "72": {
        "name": "DEVICE_NAME",
        "description": "Device name"
    },
    
    # Network and WiFi
    "100": {
        "name": "WIFI_NAME",
        "description": "WiFi network name"
    },
    "101": {
        "name": "WIFI_SIGNAL_QUALITY",
        "description": "WiFi signal quality indicator"
    },
    "102": {
        "name": "ROTATE_ENABLE",
        "description": "Rotation enable"
    },
    "103": {
        "name": "LED_ENABLE", 
        "description": "LED enable"
    },
    "104": {
        "name": "SD_RECORD_TYPE",
        "description": "SD recording type"
    },
    "105": {
        "name": "SD_RECORD_DURATION",
        "description": "SD recording duration"
    },
    "106": {
        "name": "PIR_DET_ENABLE",
        "description": "PIR detection enable"
    },
    "107": {
        "name": "PIR_DET_SENSITIVITY",
        "description": "PIR detection sensitivity"
    },
    "108": {
        "name": "HUMAN_DET_ENABLE",
        "description": "Human detection enable"
    },
    "109": {
        "name": "SOUND_DET_ENABLE",
        "description": "Sound detection enable"
    },
    "110": {
        "name": "SOUND_DET_SENSITIVITY",
        "description": "Sound detection sensitivity"
    },
    "111": {
        "name": "CRY_DET_ENABLE",
        "description": "Cry detection enable"
    },
    "112": {
        "name": "HUMAN_TRACK_ENABLE",
        "description": "Human tracking enable"
    },
    "113": {
        "name": "DAY_NIGHT_MODE",
        "description": "Day/night mode"
    },
    "114": {
        "name": "SD_STATUS",
        "description": "SD card status"
    },
    "115": {
        "name": "SD_CAPACITY",
        "description": "SD card capacity"
    },
    "116": {
        "name": "SD_REMAINING_CAPACITY", 
        "description": "SD remaining capacity"
    },
    "117": {
        "name": "HUMAN_FRAME_ENABLE",
        "description": "Human frame enable"
    },
    "118": {
        "name": "SLEEP_MODE",
        "description": "Sleep mode"
    },
    "119": {
        "name": "SLEEP_TIME_LIST",
        "description": "Sleep time schedule"
    },
    "120": {
        "name": "SLEEP_WIFI",
        "description": "Sleep WiFi"
    },
    "121": {
        "name": "ONVIF_ENABLE",
        "description": "ONVIF enable"
    },
    "122": {
        "name": "ONVIF_PORT",
        "description": "ONVIF port"
    },
    "123": {
        "name": "ONVIF_URL",
        "description": "ONVIF URL"
    },
    "124": {
        "name": "H265_ENABLE",
        "description": "H.265 encoding enable"
    },
    "125": {
        "name": "ALARM_PLAN_LIST",
        "description": "Alarm plan list"
    },
    "126": {
        "name": "IP_ADDRESS",
        "description": "IP address"
    },
    "127": {
        "name": "NET_MODE",
        "description": "Network connection mode"
    },
    "128": {
        "name": "OTA_UPGRADE_STATUS",
        "description": "Update status"
    },
    "130": {
        "name": "RTMP_STREAM",
        "description": "RTMP stream"
    },
    "131": {
        "name": "CHIME_PRO_RING_URI",
        "description": "Chime Pro ring URI"
    },
    "132": {
        "name": "CHIME_PRO_RING_ENABLE",
        "description": "Chime Pro ring enable"
    },
    "133": {
        "name": "CHIME_PRO_MOTION_URI",
        "description": "Chime Pro motion URI"
    },
    "134": {
        "name": "CHIME_PRO_MOTION_ENABLE",
        "description": "Chime Pro motion enable"
    },
    "135": {
        "name": "CHIME_PRO_RING_TYPE",
        "description": "Chime Pro ring type"
    },
    "136": {
        "name": "CHIME_PRO_SNOOZE_INTERVAL",
        "description": "Chime Pro snooze interval"
    },
    "137": {
        "name": "CHIME_PLAN",
        "description": "Chime plan"
    },
    "140": {
        "name": "RECORD_SWITCH",
        "description": "Recording switch"
    },
    "141": {
        "name": "SMART_DET",
        "description": "Smart detection"
    },
    "142": {
        "name": "SMART_DET_FRAME",
        "description": "Smart detection frame"
    },
    "143": {
        "name": "SMART_DET_SENSITIVITY",
        "description": "Smart detection sensitivity"
    },
    "145": {
        "name": "TIMING_RECORD",
        "description": "Timing record"
    },
    
    # PIR and Detection
    "150": {
        "name": "MOTION_DET_ENABLE",
        "description": "Motion detection enable"
    },
    "151": {
        "name": "MOTION_DET_SENSITIVITY",
        "description": "Motion detection sensitivity"
    },
    "152": {
        "name": "SPEAK_VOLUME",
        "description": "Speaker volume"
    },
    "153": {
        "name": "POWER_TYPE",
        "description": "Power supply type"
    },
    "154": {
        "name": "BATTERY_PERCENT",
        "description": "Battery percentage"
    },
    "155": {
        "name": "BATTERY_REMAINING",
        "description": "Remaining battery"
    },
    "156": {
        "name": "CHARGE_STATUS",
        "description": "Charging status"
    },
    "157": {
        "name": "WIRELESS_CHIME_ENABLE",
        "description": "Wireless chime enable"
    },
    "158": {
        "name": "WIRELESS_CHIME_VOLUME",
        "description": "Wireless chime volume"
    },
    "159": {
        "name": "WIRELESS_CHIME_SONGS",
        "description": "Available wireless chime songs"
    },
    "160": {
        "name": "WIRELESS_CHIME_SONG_SELECTED",
        "description": "Selected wireless chime song"
    },
    "161": {
        "name": "MECHANICAL_CHIME_ENABLE",
        "description": "Mechanical chime enable"
    },
    "162": {
        "name": "TIME_FORMAT_SWITCH",
        "description": "Time format switch"
    },
    "163": {
        "name": "BELL_SLEEP_DELAY",
        "description": "Bell sleep delay"
    },
    "164": {
        "name": "BELL_ENTER_MESSAGE_TIME",
        "description": "Bell enter message time"
    },
    "165": {
        "name": "BELL_MAX_MESSAGE_TIME",
        "description": "Bell max message time"
    },
    "166": {
        "name": "BELL_RING_ENABLE",
        "description": "Bell ring enable"
    },
    "167": {
        "name": "FRONT_LIGHT_SWITCH",
        "description": "Front light switch"
    },
    "168": {
        "name": "FRONT_LIGHT_BRIGHTNESS",
        "description": "Front light brightness"
    },
    "169": {
        "name": "FRONT_LIGHT_SCHEDULE",
        "description": "Front light schedule"
    },
    "170": {
        "name": "DOUBLE_PIR_STATUS",
        "description": "Double PIR status"
    },
    "171": {
        "name": "FRONT_LIGHT_PIR_DURATION",
        "description": "Front light PIR duration"
    },
    "172": {
        "name": "DEVICE_LOCK_STATUS",
        "description": "Device lock status"
    },
    "173": {
        "name": "HUMAN_DET_NIGHT_ENABLE",
        "description": "Human detection night enable"
    },
    "174": {
        "name": "HUMAN_DET_DAY_ENABLE",
        "description": "Human detection day enable"
    },
    "175": {
        "name": "BACKUP_MAC_ADDRESS",
        "description": "Backup MAC address"
    },
    "176": {
        "name": "NETWORK_TIMEOUT",
        "description": "Network timeout setting"
    },
    "177": {
        "name": "ROI",
        "description": "Region of Interest"
    },
    "178": {
        "name": "ALARM_FREQUENCY",
        "description": "Alarm frequency"
    },
    "179": {
        "name": "MUSIC_VOLUME",
        "description": "Music volume"
    },
    "180": {
        "name": "FRONT_LIGHT_LINK_ENABLE",
        "description": "Front light link enable"
    },
    "181": {
        "name": "FACE_RECOGNITION_SWITCH",
        "description": "Face recognition switch"
    },
    "182": {
        "name": "SOUND_LIGHT_ENABLE",
        "description": "Sound light enable"
    },
    "183": {
        "name": "SOUND_LIGHT_TYPE",
        "description": "Sound light type"
    },
    "184": {
        "name": "SOUND_LIGHT_DURATION",
        "description": "Sound light duration"
    },
    "185": {
        "name": "FRONT_LIGHT_MANUAL_DURATION",
        "description": "Front light manual duration"
    },
    "186": {
        "name": "FRONT_LIGHT_MULTI_SCHEDULE",
        "description": "Front light multi schedule"
    },
    "187": {
        "name": "FRONT_LIGHT_LINK_SIREN_ENABLE",
        "description": "Front light link siren enable"
    },
    "190": {
        "name": "MARK_TIME",
        "description": "OSD enable"
    },
    "191": {
        "name": "LOGO_SWITCH",
        "description": "Logo switch"
    },
    "192": {
        "name": "DEVICE_BRIGHTNESS",
        "description": "Device brightness setting"
    },
    "193": {
        "name": "PIR_SENSITIVITY_LEVEL",
        "description": "PIR sensitivity level"
    },
    "194": {
        "name": "PIR_SENSITIVITY_TIME",
        "description": "PIR sensitivity time"
    },
    "195": {
        "name": "ABNORMAL_NOISE_ENABLE",
        "description": "Abnormal noise enable"
    },
    "196": {
        "name": "ABNORMAL_NOISE_SENSITIVITY",
        "description": "Abnormal noise sensitivity"
    },
    "197": {
        "name": "ABNORMAL_NOISE_DETECTION",
        "description": "Abnormal noise detection"
    },
    "198": {
        "name": "RGB_LIGHT_COLOR",
        "description": "RGB light color"
    },
    "199": {
        "name": "RGB_LIGHT_TIMING",
        "description": "RGB light timing"
    },
    "200": {
        "name": "RGB_LIGHT_MODE",
        "description": "RGB light mode"
    },
    "201": {
        "name": "SENSITIVITY_LEVEL",
        "description": "Sensitivity level"
    },
    "202": {
        "name": "SENSITIVITY_TIME",
        "description": "Sensitivity time"
    },
    "203": {
        "name": "AUTO_UPDATE",
        "description": "Automatic update"
    },
    "204": {
        "name": "BATTERY_MANAGER",
        "description": "Battery manager"
    },
    "205": {
        "name": "DEVICE_WAKE",
        "description": "Device wake statistics"
    },
    "206": {
        "name": "HOMEKIT_ENABLE",
        "description": "HomeKit enable"
    },
    "207": {
        "name": "SEN_SOUND_ENABLE",
        "description": "Sensor sound enable"
    },
    "208": {
        "name": "SEN_SOUND",
        "description": "Sensor sound"
    },
    "209": {
        "name": "FULL_COLOR_MODE",
        "description": "Full color mode"
    },
    "211": {
        "name": "RAE_SOUND",
        "description": "RAE sound"
    },
    "212": {
        "name": "BELL_PHONE",
        "description": "Bell phone"
    },
    "213": {
        "name": "NVR_NEUTRAL_CHANNEL_MAX",
        "description": "NVR neutral channel max"
    },
    "214": {
        "name": "NVR_NEUTRAL_QR_CODE_STRING",
        "description": "NVR neutral QR code string"
    },
    "215": {
        "name": "NVR_NEUTRAL_DISK_STATUS",
        "description": "NVR neutral disk status"
    },
    "216": {
        "name": "VIDEO_PASSWORD_SET",
        "description": "Video password set"
    },
    "217": {
        "name": "VIDEO_PASSWORD",
        "description": "Video password"
    },
    "218": {
        "name": "WHISTLE_TIME",
        "description": "Whistle time"
    },
    "219": {
        "name": "TIME_ZONE_AUTO",
        "description": "Automatic time zone"
    },
    "220": {
        "name": "TIME_ZONE_GMT",
        "description": "GMT time zone"
    },
    "221": {
        "name": "DEVICE_ICCID",
        "description": "Device ICCID"
    },
    "222": {
        "name": "DEVICE_IMEI",
        "description": "Device IMEI"
    },
    "223": {
        "name": "HUMAN_SENSITIVITY",
        "description": "Human sensitivity"
    },
    "224": {
        "name": "HUMAN_SENSITIVITY_LEVEL",
        "description": "Human sensitivity level"
    },
    "228": {
        "name": "FRAME_RATE",
        "description": "Frame rate"
    },
    "230": {
        "name": "SOUND_LIGHT_ALARM_PLAN_LIST",
        "description": "Sound light alarm plan list"
    },
    "231": {
        "name": "LIGHT_SOUND_SELECT_SONG",
        "description": "Light sound select song"
    },
    "232": {
        "name": "POLYGON_ROI",
        "description": "Polygon ROI"
    },
    "234": {
        "name": "AI_DETECTION",
        "description": "AI detection"
    },
    "235": {
        "name": "UPLOAD_VIDEO",
        "description": "Upload video"
    },
    "236": {
        "name": "PET_THROW_WARNING",
        "description": "Pet throw warning"
    },
    "238": {
        "name": "INFRARED_LIGHT",
        "description": "Infrared light"
    },
    "241": {
        "name": "TEMP_HUMIDITY_ENABLE",
        "description": "Temperature humidity enable"
    },
    "242": {
        "name": "PIR_TRIGGER_INTERVAL",
        "description": "PIR trigger interval"
    },
    "243": {
        "name": "PIR_TRIGGER_SCHEDULE",
        "description": "PIR trigger schedule"
    },
    "244": {
        "name": "KEY_VOICE_SWITCH",
        "description": "Key voice switch"
    },
    "245": {
        "name": "KEY_VOICE_SCHEDULE",
        "description": "Key voice schedule"
    },
    "247": {
        "name": "SMART_POLYGON_ROI",
        "description": "Smart polygon ROI"
    },
    "251": {
        "name": "RESTORE_FACTORY",
        "description": "Factory reset"
    },
    "256": {
        "name": "LANGUAGE",
        "description": "Language"
    },
    "263": {
        "name": "PET_ALARM",
        "description": "Pet alarm"
    },
    "264": {
        "name": "PET_ALARM_ENABLE",
        "description": "Pet alarm enable"
    },
    "266": {
        "name": "LOW_THRESHOLD",
        "description": "Low threshold"
    },
    "267": {
        "name": "FULL_TIME_FRAME_RATE",
        "description": "Full time frame rate"
    },
    "272": {
        "name": "AOV_RECORD_DELAY",
        "description": "AOV record delay"
    },
    "274": {
        "name": "AOV_NIGHT_MODE",
        "description": "AOV night mode"
    },
    "275": {
        "name": "AOV_WORK_MODE",
        "description": "AOV work mode"
    },
    "276": {
        "name": "AOV_WAKE_MODE",
        "description": "AOV wake mode"
    },
    "277": {
        "name": "AOV_BATTERY_MODE",
        "description": "AOV battery mode"
    },
    "299": {
        "name": "SMART_DET_FRONT",
        "description": "Smart detection front"
    },
    "300": {
        "name": "SMART_DET_FRONT_FRAME",
        "description": "Smart detection front frame"
    },
    "301": {
        "name": "SMART_DET_FRONT_SENSITIVITY",
        "description": "Smart detection front sensitivity"
    },
    "303": {
        "name": "SMART_FRONT_POLYGON_ROI",
        "description": "Smart front polygon ROI"
    },
    "305": {
        "name": "IMAGE_QUALITY",
        "description": "Image quality"
    },
    "309": {
        "name": "TIME_ZONE_INT",
        "description": "Time zone integer"
    },
    "313": {
        "name": "ALARM_MODE",
        "description": "Alarm mode"
    },
    "316": {
        "name": "ANIMAL_DET_ENABLE",
        "description": "Animal detection enable"
    },
    "317": {
        "name": "ANIMAL_SENSITIVITY_LEVEL",
        "description": "Animal sensitivity level"
    },
    "319": {
        "name": "BELL_RING_SWITCH",
        "description": "Bell ring switch"
    },
    "321": {
        "name": "NIGHT_EYE_STRONG",
        "description": "Night eye strong"
    },
    "322": {
        "name": "STATIC_EYE_STRONG",
        "description": "Static eye strong"
    },
    "325": {
        "name": "SOUND_LIGHT_DURATION",
        "description": "Sound light duration"
    },
    "332": {
        "name": "DEVICE_RESOLUTION",
        "description": "Device resolution"
    },
    "334": {
        "name": "WIFI_FREQUENCY",
        "description": "WiFi frequency"
    },
    
    # Control Commands (800+ range)
    "800": {
        "name": "RESET_DEVICE",
        "description": "Reset device"
    },
    "802": {
        "name": "SYNC_TIME",
        "description": "Sync time"
    },
    "803": {
        "name": "OTA_UPGRADE",
        "description": "OTA upgrade"
    },
    "806": {
        "name": "FORMAT_SDCARD",
        "description": "Format SD card"
    },
    "807": {
        "name": "START_PTZ",
        "description": "Start PTZ"
    },
    "808": {
        "name": "STOP_PTZ",
        "description": "Stop PTZ"
    },
    "809": {
        "name": "REFRESH_PROPERTY",
        "description": "Refresh property"
    },
    "810": {
        "name": "DEVICE_INFO",
        "description": "Device hardware information"
    },
    "811": {
        "name": "PAIR_WIRELESS_CHIME",
        "description": "Pair wireless chime"
    },
    "812": {
        "name": "UNPAIR_WIRELESS_CHIME",
        "description": "Unpair wireless chime"
    },
    "813": {
        "name": "UNLOCK_BATTERY",
        "description": "Unlock battery"
    },
    "816": {
        "name": "RELAY",
        "description": "Relay"
    },
    "821": {
        "name": "PTZ_PRESET_SET",
        "description": "PTZ preset set"
    },
    "822": {
        "name": "PTZ_PATROL",
        "description": "PTZ patrol"
    },
    "823": {
        "name": "FRONT_LIGHT_SIREN_SWITCH",
        "description": "Front light siren switch"
    },
    "824": {
        "name": "RELAY_STATUS",
        "description": "Relay status"
    },
    "825": {
        "name": "ALL_ALARMS",
        "description": "All alarms"
    },
    "827": {
        "name": "JINGLE_PLAN",
        "description": "Jingle plan"
    },
    "828": {
        "name": "JINGLE_PLAN_ENABLE",
        "description": "Jingle plan enable"
    },
    "830": {
        "name": "REMOVE_NVR_IPC",
        "description": "Remove NVR IPC"
    },
    "831": {
        "name": "ALLOW_DISCOVERED",
        "description": "Allow discovered"
    },
    "832": {
        "name": "NVR_START_SEARCH_DEVICE",
        "description": "NVR start search device"
    },
    "833": {
        "name": "NVR_ADD_DEVICE",
        "description": "NVR add device"
    },
    "834": {
        "name": "NVR_ALL_DAY_RECORD",
        "description": "NVR all day record"
    },
    "835": {
        "name": "NVR_ALL_ALARM",
        "description": "NVR all alarm"
    },
    "836": {
        "name": "NVR_REQUEST_ADD_DEVICE",
        "description": "NVR request add device"
    },
    "837": {
        "name": "RECEPTACLE_SWITCH",
        "description": "Receptacle switch"
    },
    "841": {
        "name": "PET_FEEDER_STATUS",
        "description": "Pet feeder status"
    },
    "842": {
        "name": "PET_FEEDER_CONTROL",
        "description": "Pet feeder control"
    },
    "843": {
        "name": "PET_SOUND_SET",
        "description": "Pet sound set"
    },
    "844": {
        "name": "PET_FEED_CALL",
        "description": "Pet feed call"
    },
    "845": {
        "name": "PET_FEED",
        "description": "Pet feed"
    },
    "846": {
        "name": "JINGLE_AUTO_ADD_DEVICE",
        "description": "Jingle auto add device"
    },
    "847": {
        "name": "PTZ_CALIBRATION",
        "description": "PTZ calibration"
    },
    "848": {
        "name": "PTZ_PRESET",
        "description": "PTZ preset"
    },
    "849": {
        "name": "CAMERA_LINKAGE",
        "description": "Camera linkage"
    },
    "850": {
        "name": "PET_FEED_2",
        "description": "Pet feed 2"
    },
    "851": {
        "name": "CAMERA_CONTROL",
        "description": "Camera control"
    },
    
    # Status Parameters (1000+ range)
    "1000": {
        "name": "DEVICE_INIT_STATUS",
        "description": "Device initialization status"
    },
    "1001": {
        "name": "UPDATE_DOWNLOAD",
        "description": "Update download"
    },
    "1002": {
        "name": "UPDATE_PROGRESS",
        "description": "Update in progress"
    },
    "1003": {
        "name": "UPDATE_TOTAL",
        "description": "Update total"
    },
    "1004": {
        "name": "SD_FORMAT_PROGRESS",
        "description": "SD card format progress"
    },
    "1007": {
        "name": "WIFI_STRENGTH",
        "description": "WiFi signal strength"
    },
    "1008": {
        "name": "TEMPERATURE",
        "description": "Temperature"
    },
    "1009": {
        "name": "HUMIDITY",
        "description": "Humidity"
    },
    "1010": {
        "name": "FRONT_LIGHT_STATUS",
        "description": "Front light status"
    },
    "1012": {
        "name": "DEVICE_TEMPERATURE",
        "description": "Device temperature"
    },
    "1013": {
        "name": "FRONT_LIGHT_SIREN_DURATION",
        "description": "Front light siren duration"
    },
    "1014": {
        "name": "RGB_LIGHT_STATUS",
        "description": "RGB light status"
    },
    "1015": {
        "name": "NVR_NEUTRAL_CHANNEL_STATUS",
        "description": "NVR neutral channel status"
    },
    "1018": {
        "name": "ALLOW_DISCOVERED_TIME_LEFT",
        "description": "Allow discovered time left"
    },
    "1019": {
        "name": "NVR_GET_SEARCH_RESULT",
        "description": "NVR get search result"
    },
    "1020": {
        "name": "SLEEP_STATUS",
        "description": "Sleep status"
    },
    "1026": {
        "name": "ERROR_MESSAGES",
        "description": "Error messages"
    },
    "1028": {
        "name": "DEVICE_CONFIG_ERROR_CODE",
        "description": "Device configuration error code"
    },
    "1030": {
        "name": "WIFI_CONNECT_STATE",
        "description": "WiFi connection state"
    },
    "1031": {
        "name": "WIFI_INFO_EXT",
        "description": "Extended WiFi information"
    },
    "1032": {
        "name": "DEVICE_WIFI_INFO",
        "description": "Device WiFi information"
    },
    "1034": {
        "name": "PTZ_INFO",
        "description": "PTZ information"
    }
}

# Value mappings for specific parameters
VALUE_MAPPINGS = {
    "DAY_NIGHT_MODE": {
        "0": "Auto",
        "1": "Day", 
        "2": "Night"
    },
    "SD_STATUS": {
        "0": "No Card",
        "1": "Normal", 
        "2": "Error",
        "3": "Full"
    },
    "DEVICE_RESOLUTION": {
        "0": "720P",
        "1": "1080P",
        "2": "2K", 
        "3": "4K"
    },
    "NET_MODE": {
        "0": "WiFi",
        "1": "Ethernet",
        "2": "4G"
    },
    "POWER_TYPE": {
        "0": "Battery",
        "1": "Adapter",
        "2": "PoE"
    },
    "BOOLEAN": {
        "0": "Disabled",
        "1": "Enabled"
    }
}

# Parameters that use boolean formatting
BOOLEAN_PARAMETERS = {
    "LED_ENABLE", "PIR_DET_ENABLE", "ONVIF_ENABLE", "MOTION_DET_ENABLE", 
    "CLOUD_UPLOAD_ENABLE", "H265_ENABLE", "ROTATE_ENABLE", "HUMAN_DET_ENABLE",
    "SOUND_DET_ENABLE", "CRY_DET_ENABLE", "HUMAN_TRACK_ENABLE", "RECORD_SWITCH",
    "WIRELESS_CHIME_ENABLE", "MECHANICAL_CHIME_ENABLE", "FACE_RECOGNITION_SWITCH",
    "SOUND_LIGHT_ENABLE", "FRONT_LIGHT_SWITCH", "AUTO_UPDATE", "HOMEKIT_ENABLE",
    "TIME_ZONE_AUTO", "ABNORMAL_NOISE_ENABLE", "ANIMAL_DET_ENABLE", 
    "BELL_RING_SWITCH", "ALLOW_DISCOVERED", "TEMP_HUMIDITY_ENABLE"
}

# Parameters that represent percentages
PERCENTAGE_PARAMETERS = {
    "BATTERY_PERCENT", "WIFI_STRENGTH", "BATTERY_REMAINING"
}

# Parameters that represent capacity in MB/GB  
CAPACITY_PARAMETERS = {
    "SD_CAPACITY", "SD_REMAINING_CAPACITY"
}

def get_parameter_name(code: str) -> str:
    """Get human-readable name for parameter code."""
    param_info = IOT_PARAMETERS.get(str(code))
    if param_info:
        return param_info["name"]
    return f"iot_{code}"

def get_parameter_description(code: str) -> str:
    """Get description for parameter code."""
    param_info = IOT_PARAMETERS.get(str(code))
    if param_info:
        return param_info["description"]
    return f"Unknown parameter {code}"

def get_parameter_code_by_name(param_name: str) -> str:
    """Get IoT parameter code by human readable name."""
    param_name = param_name.upper().strip()
    for code, info in IOT_PARAMETERS.items():
        if info["name"] == param_name:
            return code
    return None

def format_parameter_value(param_name: str, value, debug_mode: bool = False) -> str:
    """Format parameter value for display."""
    try:
        # Handle boolean parameters
        if param_name in BOOLEAN_PARAMETERS:
            return VALUE_MAPPINGS["BOOLEAN"].get(str(value), str(value))
        
        # Handle percentage parameters
        if param_name in PERCENTAGE_PARAMETERS:
            return f"{value}%"
            
        # Handle capacity parameters
        if param_name in CAPACITY_PARAMETERS:
            try:
                mb_value = float(value)
                if mb_value >= 1024:
                    return f"{mb_value/1024:.3f}GB"
                return f"{mb_value}MB"
            except (ValueError, TypeError):
                return str(value)
        
        # Handle specific value mappings
        if param_name in VALUE_MAPPINGS:
            return VALUE_MAPPINGS[param_name].get(str(value), str(value))
            
        # Handle JSON values
        if isinstance(value, str) and (value.startswith('{') or value.startswith('[')):
            try:
                import json
                parsed = json.loads(value)
                if debug_mode:
                    return json.dumps(parsed, indent=2)
                else:
                    if isinstance(parsed, dict):
                        return f"JSON object with {len(parsed)} keys"
                    elif isinstance(parsed, list):
                        return f"JSON array with {len(parsed)} items"
            except json.JSONDecodeError:
                pass
                
        return str(value)
    except Exception:
        return str(value)

def get_all_parameter_codes() -> dict:
    """Get all parameter codes with their names and descriptions."""
    return IOT_PARAMETERS

def search_parameters(search_term: str) -> dict:
    """Search parameters by name or description."""
    results = {}
    search_lower = search_term.lower()
    
    for code, info in IOT_PARAMETERS.items():
        if (search_lower in info["name"].lower() or 
            search_lower in info["description"].lower()):
            results[code] = info
            
    return results
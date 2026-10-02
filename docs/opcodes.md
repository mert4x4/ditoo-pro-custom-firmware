# BLE opcodes of the Ditoo Pro (stock firmware 24020)

Worked out from the stock firmware and merged with other sources:

- **fw**: the command dispatcher and model handlers of stock firmware 24020 (the custom firmware has the same tables and points 0xF0 at the remote mode code),
- **app**: the Divoom Android app 3.8.40 (names from `SppProc$CMD_TYPE`, via futpib's catalogue <https://github.com/futpib/divoom-ditoo-pro-controller>),
- **docs**: Divoom's Bluetooth protocol documentation <https://docin.divoom-gz.com/web/#/5/178>,
- **hass**: hass-divoom <https://github.com/d03n3rfr1tz3/hass-divoom> (issue #17 for the Ditoo),
- **repo**: what this project's client, Home Assistant integration and flasher use and were tested with on the device,
- **timebox**: older projects for the Timebox / Timebox Evo (fhem-Divoom, RomRider's node-divoom-timebox-evo); field meanings that match the Ditoo Pro's use of the opcode but were not checked on this device.

The brackets after each note name its source.

## Packets

- Plain: `01, len LE16, opcode, data, sum LE16, 02`; `len` counts opcode..sum, `sum` is the byte sum of `len`..data. The Ditoo Pro uses no byte escaping. The Android app sends this over BLE too, in 20-byte writes.
- iOS LE format (used by `client/ditoo_api.py`): header `FE EF AA 55`, with a packet number and ACK `0x33`; see Divoom's documentation. The device accepts both.
- Replies come as opcode **0x04**: `[4]` = command answered, `[5]` = `0x55` on success, data from `[6]`. The **Reply** column describes that data; **fw** lists the command bytes the firmware answers with.
- Many setters read when the data byte is **0xFF** (0xFFFF for 16-bit values); the reply carries the current value.

## How the firmware handles a command

1. The dispatcher (`0x398c0`) jumps through the table at `0x399c4` for opcodes `0x05`..`0xfb`. Opcodes outside that range and entries pointing at `0x3b2b6` are ignored without a reply.
2. A few handlers do the work themselves (**direct**). Most fill a 12-byte message and call `divoom_sys_model_crl_msg_push` (`0x4a3b0`): `msg[2]` is a **model** id, `msg[4]` a **command** number in that model, `msg[8]` points at the payload. The dispatcher leaves `msg[0]` at 0, which makes the call synchronous: the model function runs at once, in the dispatcher task, and switches on the command.
3. The **Firmware** column shows the handler address, that route and the case address, the firmware functions called there (named after the function-name strings they log), the reply opcodes and log strings. "no case: ignored" means the model has no case for that command, so the opcode does nothing.

## Opcodes

**App**: whether the Android app sends the opcode and parses replies to it.

| Op | Name | Data | Reply | Notes | Client | App | Firmware |
|---|---|---|---|---|---|---|---|
| `0x05` | `SPP_CHANGE_MODE`<br>docs: Set work mode | work mode (docs list 0..11): 0 Bluetooth, 2 line in, 3 SD card, 7 USB audio (UAC) switch modes [fw, app]; 1 FM, 4 USB host, 5 record, 6 record play and 8+ are ignored [fw] |  | posted to the main task as message 0x7f2a and mapped to SDK modes (BtPlay, LinePlay, CardPlay, UsbDevicePlay); no FM (RadioPlay) or USB host (UDiskPlay) mode in this build; a mode needs its hardware bit (e.g. card inserted, line-in jack presence unverified); skipped while the device is busy (logs "divoom pass the msg_mode!") or already in that mode [fw] | `set_work_mode` | sends | `0x3a1a0`<br>direct |
| `0x06` | docs: get sd play name |  | SD track name | SD card |  | no | `0x3b088`<br>direct<br>replies `0x06` |
| `0x07` | `SPP_GET_SD_MUSIC_LIST`<br>docs: Get sd music list | first LE16, last LE16 [app] | track list entries; 0x14 after the last [app] |  |  | sends, parses | `0x3b0fc`<br>direct<br>`divoom_os_com_get_sd_play_music_list`<br>replies `0x14` |
| `0x08` | `SPP_SET_VOL`<br>docs: Set vol | volume 0..15 [app, repo] |  |  | `set_volume` | sends | `0x3b208`<br>direct |
| `0x09` | `SPP_GET_VOL`<br>docs: Get vol |  | volume [app] |  | `get_volume` | sends, parses | `0x3b210`<br>direct<br>replies `0x09` |
| `0x0a` | `SPP_SET_PLAY_STATUS`<br>docs: Set play status | 1 play, 0 pause [app] | play status [app] |  | `set_playing` | sends, parses | `0x3b234`<br>direct |
| `0x0b` | `SPP_GET_PLAY_STATUS`<br>docs: Get play status |  | play status [app] |  | `get_playing` | sends, parses | `0x3b23c`<br>direct<br>replies `0x0b` |
| `0x10` | ? |  |  | not named or sent by the app; factory/test? [fw] |  | no | `0x3a1ac`<br>direct<br>replies `0x10` |
| `0x11` | `SPP_SET_SD_PLAY_MUSIC_ID`<br>docs: Set sd play music id | track LE16 [app] |  |  | `sd_play_track` | sends | `0x3b158`<br>direct |
| `0x12` | `SPP_SET_LAST_NEXT`<br>docs: Set sd last next | 1 next, 0 previous [app, repo] |  |  | `next_track` | sends | `0x3b200`<br>direct |
| `0x13` | `SPP_GET_STDB_MODE`<br>docs: Get working mode | none [app] | work mode: 0 Bluetooth, 2 line in, 3 SD card, 7 USB audio, 8 during a phone call (BtHfPlay), 0 for record/idle modes [fw] | 8 is readable but cannot be set with 0x05; usable as an "in a call" state; the device does not announce mode changes, poll 0x13 [fw] | `get_work_mode` | sends, parses | `0x3a1c8`<br>direct<br>replies `0x13` |
| `0x15` | `SPP_SEND_SD_TF_STATUS`<br>docs: Send sd status |  | 1 = card present [app] | the device also sends it when the card changes [app] | `get_sd_card` | sends, parses | `0x3b25c`<br>direct<br>replies `0x15` |
| `0x16` | `SPP_SET_GIF_SPEED_CMD`<br>docs: Set gif speed | animation speed LE16 [app] |  |  | `set_animation_speed` | sends | `0x3a1e4`<br>model `0x09` cmd `0x05` → `0x43248`<br>`divoom_draw_malloc_resource` |
| `0x17` | `SPP_SEND_GAME_CTRL_INFO`<br>docs: Set game ctrl info | key: 1 left, 2 right, 3 up, 4 down, 5 switch [app] |  | game key down; key up is 0x21 [app] | `game_key` | sends | `0x3ad80`<br>model `0x08` cmd `0x02` → `0x38936` |
| `0x18` | `SPP_SET_SYSTEM_TIME` | year%100, year/100, month, day, hour, minute, second, weekday (0 = Sunday) [app] |  | `client.set_time` leaves out the weekday; the device asks for the time with BD 2E and the app answers with 0x18 [app] | `set_time` | sends | `0x3a200`<br>direct<br>`divoom_os_com_update_system_time`; `divoom_sys_model_crl_send_asyn_all_model`<br>replies `0x18`<br>log `Fwl_RTCSetTime ok !!!` |
| `0x1a` | `SPP_STOP_SEND_GIF` |  |  | the app never sends it [app]; download model 0x0b [fw] |  | no | `0x3a284`<br>model `0x0b` cmd `0x0e` → `0x38524`<br>`divoom_file_close`; `divoom_app_download_end_cur_file`<br>replies `0x48` |
| `0x1b` | `SPP_APP_EQ_GIF`<br>docs: App send eq gif |  |  | equaliser animation; the app never sends it [app] |  | no | `0x3a29c`<br>model `0x0b` cmd `0x0d` → `0x38398`<br>`divoom_app_download_gif_add_data` |
| `0x1c` | `SPP_SET_POWERON_VOICE_CTRL` | 1 byte [app] | 1 = event [app] | power-on voice on/off [app]; the firmware routes it to the alarm file case of model 0x0b, like 0x82 [fw] |  | sends, parses | `0x3a300`<br>model `0x0b` cmd `0x1a` → `0x38400`<br>`divoom_alarm_file_manage_del`; `divoom_alarm_file_manage_open_write`; `divoom_alarm_file_manage_write_close`<br>replies `0x00`<br>log `a2dp isn't ready! we will exit!` |
| `0x1f` | `SPP_SPP_POWER_ON_OFF_INFO` | `00` read; `01, slot, enabled, type, hour, minute, weekdays, R, G, B`; type 1 = power on (the app shows the colour), 0 = power off [app] | schedule [app] | scheduled screen on/off (Discover "power on/off"); calls `divoom_power_control_set` [fw, app] |  | sends, parses | `0x3acaa`<br>direct<br>`divoom_power_control_set` |
| `0x21` | `SPP_SEND_GAME_CTRL_KEY_UP_INFO`<br>docs: Set game ctrl key up info | key, as 0x17 [app] |  | game key up [app] |  | sends | `0x3adbc`<br>direct |
| `0x23` | `SPP_SPP_LIGHT_ARROW_SWITCH` | 00 previous effect, 01 next, 02 toggle [fw, app, hass] |  | key backlight; 50 effects with wrap-around, no absolute on/off [fw] | `keyboard_light` | sends | `0x39da0`<br>model `0x11` cmd `0x2f` → `0x46a9c` |
| `0x26` | `SPP_SEND_APP_NEWEST_TIME` | 1/0; FF read [app] | 1/0 [app] | hass-divoom sends it after the clock channel as "set hot" [hass] |  | sends, parses | `0x3a4cc`<br>model `0x11` cmd `0x03` → `0x47184` |
| `0x27` | `SPP_SET_APP_BR_PASSWORD`<br>docs: Set blue password | `00` off; `01 d1 d2 d3 d4` four-digit password; `02` read [app] | status; with 1 the 4 digits follow [app] | Bluetooth pairing password [app] | `get_password_status`, `set_password` | sends, parses | `0x3a184`<br>model `0x11` cmd `0x1b` → `0x46e70`<br>replies `0x27` |
| `0x2b` | `SPP_SET_TEMP_TYPE`<br>docs: Set temp type | 1 Fahrenheit, 0 Celsius; FF read [app, repo] | 1/0 [app] |  | `get_temperature_unit`, `set_temperature_unit` | sends, parses | `0x3a338`<br>model `0x11` cmd `0x07` → `0x47248`<br>replies `0x2b` |
| `0x2d` | `SPP_SET_HPUR_TYPE` | 1 24-hour, 0 12-hour; FF read [app, hass] | 1/0 [app] | Divoom's docs say 0x2c, which this firmware ignores [fw, docs] |  | sends, parses | `0x3a354`<br>model `0x11` cmd `0x06` → `0x4727c`<br>replies `0x2d` |
| `0x31` | `SPP_LIGHT_CURRENT_LEVEL` | none [app] | brightness [app, fw] | same case as 0x32 and 0x74 [fw] |  | sends, parses | `0x3a370`<br>model `0x11` cmd `0x04` → `0x47198`<br>`divoom_screen_bright`<br>replies `0x31`, `0x32`, `0x46` |
| `0x32` | `SPP_LIGHT_ADJUST_LEVEL` | raw bytes; FF takes a separate path [app, fw] | level, answered as 0x32 [fw] | same case as 0x31 and 0x74 [fw] |  | sends | `0x3a38c`<br>model `0x11` cmd `0x04` → `0x47198`<br>`divoom_screen_bright`<br>replies `0x31`, `0x32`, `0x46` |
| `0x35` | `SPP_SCROLL`<br>docs: Pic scan ctrl | 00, mode, speed LE16 [app] |  | scrolling [app] |  | sends | `0x3a3b0`<br>model `0x09` cmd `0x09` → `0x43308` |
| `0x3a` | `SPP_DRAWING_MUL_PAD_CTRL`<br>docs: Drawing mul pad ctrl | pad, R G B, count, pixel indexes [app] | ack [app] | multi-page drawing pad [app] |  | sends, parses | `0x3a400`<br>model `0x09` cmd `0x10` → `0x42f18`<br>`divoom_draw_malloc_resource`<br>replies `0x58` |
| `0x3c` | `SPP_SET_ANCS_NOTICE_PIC` | app type, 3 bytes from the picture number [app] |  | notification picture per app [app] |  | sends | `0x3a41c`<br>model `0x0b` cmd `0x1c` → `0x37e70` |
| `0x40` | `SPP_SET_SLEEP_TIME`<br>docs: Set sleep auto off | minutes, mode, on, FM (2 bytes, as 0x43), volume, R G B, light [app, repo] |  | sleep timer [app] | `set_sleep` | sends | `0x3a438`<br>model `0x04` cmd `0x01` → `0x4a196` |
| `0x41` | `SPP_SET_SLEEP_SCENE`<br>docs: Set sleep scene | sleep scene bytes [app] |  |  |  | sends | `0x3a454`<br>model `0x04` cmd `0x00` → `0x4a14c` |
| `0x42` | `SPP_GET_ALARM_TIME_SCENE`<br>docs: Get alarm time | none [app] | alarm records [app] |  | `get_alarms` | sends, parses | `0x3a470`<br>model `0x03` cmd `0x01` → `0x36a76`<br>replies `0x42` |
| `0x43` | `SPP_SET_ALARM_TIME_SCENE`<br>docs: Set alarm time | index, on, hour, minute, weekdays, mode, trigger, FM, volume [app, repo] |  | FM is f = MHz*10 sent as `f % 100, f / 100`, not LE16 as in `client.Alarm` [app]; Timebox-era: number, on, hour, minute, weekdays (bit 0 Sunday .. bit 6 Saturday), scene, 3 unknown bytes `01 00 00`, volume (fhem-Divoom) [timebox] | `set_alarm` | sends | `0x3a488`<br>model `0x03` cmd `0x00` → `0x36a5e`<br>`divoom_alarm_spp_set_alarm`<br>replies `0x43` |
| `0x44` | `SPP_SET_BOX_COLOR`<br>docs: Set light pic |  |  | set light picture (old devices) [docs] |  | no | `0x3a4a4`<br>model `0x09` cmd `0x01` → `0x43120`<br>`divoom_draw_malloc_resource` |
| `0x45` | `SPP_SET_BOX_MODE`<br>docs: Set light mode | channel + settings, see the 0x45 section [app, repo] |  | picks the channel; the firmware checks data[0] < 7 [fw] | `set_channel`, `show_clock`, `show_cloud`, `show_custom`, `show_light`, `show_lyric`, `show_music`, `show_vj` | sends | `0x3a4f4`<br>direct |
| `0x46` | `SPP_GET_BOX_MODE`<br>docs: Get light mode | none [app] | state, see the 0x46 section [app] | `client.LightState` has the same layout [app] | `get_state` | sends, parses | `0x3a55c`<br>model `0x11` cmd `0x01` → `0x47190`<br>replies `0x46` |
| `0x47` | `SPP_APP_NEED_GET_MUSIC_LIST`<br>docs: App need get music list | none [app] | the device sends it when the SD list can be read [app] |  |  | sends, parses | `0x3a574`<br>direct<br>replies `0x47` |
| `0x48` | `SPP_PAUSE_SYS_UPDATE_DATA` |  |  | the device sends 0x48 to stop a firmware update [app, fw]; `flash_mva.py` listens for 0x30 instead |  | parses | `0x3a594`<br>model `0x0b` cmd `0x0b` → `0x38372`<br>`divoom_app_download_end_cur_file`<br>replies `0x48` |
| `0x49` | `SPP_SET_MUL_BOX_COLOR`<br>docs: Set light phone gif | animation frames [app] |  |  |  | sends | `0x3a5a8`<br>model `0x0b` cmd `0x0d` → `0x38398`<br>`divoom_app_download_gif_add_data` |
| `0x4a` | ? |  | 0x4a [fw] | not in the app; notification model 0x12 [fw] |  | no | `0x3a5dc`<br>model `0x12` cmd `0x01` → `0x3729c`<br>`divoom_ancs_start_ble`<br>replies `0x4a` |
| `0x4b` | ? |  | 0x4b [fw] | not in the app; notification model 0x12 [fw] |  | no | `0x3a5f8`<br>model `0x12` cmd `0x02` → `0x372ba`<br>`divoom_ancs_start_ble`<br>replies `0x4b` |
| `0x50` | `SPP_SET_ANDROID_ANCS` | `type` = notification icon of app type (types >= 8 sent +1); `type, len, UTF-8 text (<= 128)` = with text [app] |  | phone notifications [app]; ANCS model 0x12 [fw] |  | sends | `0x3a610`<br>model `0x12` cmd `0x05` → `0x372d6`<br>`divoom_ancs_disp_notice_pic_ctrl`<br>replies `0x50` |
| `0x51` | `SPP_SET_ALARM_TIME_GIF`<br>docs: Set alarm gif | slot, 3 picture bytes [app] |  | alarm picture [app] |  | sends | `0x3a63c`<br>model `0x0b` cmd `0x0d` → `0x38398`<br>`divoom_app_download_gif_add_data` |
| `0x52` | `SPP_SET_BOOT_GIF`<br>docs: Set boot gif | picture data [app] |  | boot animation [app] |  | sends | `0x3a674`<br>model `0x0b` cmd `0x0d` → `0x38398`<br>`divoom_app_download_gif_add_data` |
| `0x53` | `SPP_GET_DIALY_TIME_EXT2`<br>docs: Get memorial time | none [app] | one frame per record [app, repo] | memorials (celebrations) [app] |  | sends, parses | `0x3a6b4`<br>model `0x03` cmd `0x03` → `0x36a96`<br>replies `0x53` |
| `0x54` | `SPP_SET_DIALY_TIME_EXT2`<br>docs: Set memorial time | slot, on, month, day, hour, minute, `01`, 32 bytes UTF-16BE title (15 characters + `00 00`) [app, timebox] |  | memorials (celebrations); clearing a slot sends the slot and a fixed byte string (fhem-Divoom) [timebox] | `set_memorial` | sends | `0x3a6cc`<br>model `0x03` cmd `0x02` → `0x36a7e`<br>replies `0x54` |
| `0x55` | `SPP_SET_DIALY_TIME_GIF`<br>docs: Set memorial gif | slot, on, `00 0A`, picture data [app, timebox] |  | memorial picture [app] |  | sends | `0x3a6e8`<br>model `0x0b` cmd `0x0d` → `0x38398`<br>`divoom_app_download_gif_add_data` |
| `0x56` | `SPP_SET_TIME_MANAGE_INFO`<br>docs: Set time manage info | 10 plan bytes + picture [app] |  | time planner [app] |  | sends | `0x3a71c`<br>model `0x06` cmd `0x00` → `0x4ad1e`<br>`divoom_time_plan_add_item` |
| `0x57` | `SPP_GET_TIME_MANAGE_CTRL`<br>docs: Set time manage ctrl | 1 byte [app] | 1/0 [app] | time planner control [app] | `get_time_plan` | sends, parses | `0x3a79c`<br>model `0x06` cmd `0x01` → `0x4ad26`<br>model `0x06` cmd `0x02` → `0x4ad68`<br>`divoom_time_plan_set_mode`; `divoom_config_del`; `divoom_time_plan_event`<br>replies `0x57` |
| `0x58` | `SPP_DRAWING_PAD_CTRL`<br>docs: Drawing pad ctrl | R G B, count, pixel indexes [app] | ack [app] | drawing pad [app, repo] | `drawpad_points` | sends, parses | `0x3a7ec`<br>model `0x09` cmd `0x00` → `0x43048`<br>`divoom_draw_malloc_resource` |
| `0x5a` | `SPP_DRAWING_PAD_EXIT`<br>docs: Drawing pad exit | none [app] |  | drawing pad exit [app, repo] | `drawpad_exit` | sends | `0x3a808`<br>model `0x09` cmd `0x02` → `0x43040` |
| `0x5b` | `SPP_DRAWING_ENCODE_PIC`<br>docs: Drawing mul encode single pic | index, length LE16, encoded 16x16 frame [app] |  | encoded picture (`PixelEncodeSixteen`) [app] |  | sends | `0x3a820`<br>model `0x09` cmd `0x0f` → `0x42f74`<br>`divoom_draw_malloc_resource`; `divoom_draw_event` |
| `0x5d` | `SPP_SEND_NET_TEMP_INFO`<br>docs: Send net temp | year LE16, month, day, hour, minute, count, values [app] |  | forecast [app, repo] | `set_forecast` | sends | `0x3a83c`<br>model `0x14` cmd `0x01` → `0x4bae8`<br>`divoom_weather_net_temp_update_cache` |
| `0x5e` | `SPP_SEND_NET_TEMP_DISP_INFO`<br>docs: Send net temp disp | hourly, at a set time, outdoor, ?, 0, minutes since midnight LE16 (the set time) [app] |  | weather display settings: show the weather every hour and/or at a set time [app] |  | sends | `0x3a858`<br>model `0x14` cmd `0x02` → `0x4baf0`<br>`divoom_weather_net_temp_update_disp_type` |
| `0x5f` | `SPP_SEND_CUR_NET_TEMP`<br>docs: Send current temp | temperature, weather type [app, repo] |  | current weather [app] | `set_weather` | sends | `0x3a874`<br>model `0x14` cmd `0x00` → `0x4bae0` |
| `0x6a` | `SPP_SET_MIX_MUISE_MODE` | mode bytes [app] |  | mixer [app] |  | sends | `0x3a890`<br>model `0x09` cmd `0x0a` → `0x43344`<br>`divoom_app_schedule_cancel` |
| `0x6c` | `SPP_DRAWING_ENCODE_MOVIE_PLAY`<br>docs: Drawing encode movie play | frame LE16, length LE16, encoded frame [app] |  | LED movie frame; used for native text [app, repo] | `show_native_text` | sends | `0x3a8ac`<br>model `0x09` cmd `0x0d` → `0x434b0` |
| `0x6e` | `SPP_DRAWING_CTRL_MOVIE_PLAY`<br>docs: Drawing ctrl movie play | 1 start, 0 stop [app, repo] | the device sends it when the movie ends [app] |  | `show_native_text`, `stop_native_text` | sends, parses | `0x3a8c8`<br>model `0x09` cmd `0x0c` → `0x4342c`<br>`divoom_app_schedule_cancel`<br>replies `0x6e` |
| `0x6f` | `SPP_DRAWING_MUL_PAD_ENTER`<br>docs: Drawing mul pad enter | R G B [app, repo] |  | drawing pad enter [app] | `drawpad_enter` | sends | `0x3a8e4`<br>model `0x09` cmd `0x12` → `0x430a0`<br>`divoom_draw_malloc_resource` |
| `0x71` | `SPP_GET_TOOL_INFO`<br>docs: Get tool info | tool: 0 stopwatch, 1 scoreboard, 2 noise, 3 countdown [app, repo] | tool, then: status / red LE16, blue LE16 / on / status, minutes, seconds [app] |  | `get_tool` | sends, parses | `0x3a900`<br>direct<br>`divoom_app_tool_get_event`; `divoom_app_tool_set_event`<br>replies `0x71` |
| `0x72` | `SPP_SET_TOOL_INFO`<br>docs: Set tool info | `00 x` stopwatch, `01 on red LE16 blue LE16` scoreboard, `02 x` noise, `03 on min sec` countdown [app, repo] |  |  | `countdown`, `noise_meter`, `scoreboard_tool`, `stopwatch` | sends | `0x3a974`<br>direct<br>`divoom_app_tool_set_event`<br>replies `0x72` |
| `0x73` | `SPP_GET_NET_TEMP_DISP_INFO`<br>docs: Get net temp disp | none [app] | as 0x5e: hourly, at a set time, outdoor, ?, ?, minutes LE16 [app] |  |  | sends, parses | `0x3a998`<br>model `0x14` cmd `0x03` → `0x4baf8`<br>replies `0x73` |
| `0x74` | `SPP_SET_SYSTEM_BRIGHT`<br>docs: Set brightness | level [app, repo] |  | same case as 0x31 and 0x32; the device then reports state with 0x46 [fw] | `set_brightness` | sends | `0x3a9b0`<br>model `0x11` cmd `0x04` → `0x47198`<br>`divoom_screen_bright`<br>replies `0x31`, `0x32`, `0x46` |
| `0x75` | `SPP_SET_DEVICE_NAME`<br>docs: Set device name | length, name bytes [app, repo] |  |  | `set_name` | sends | `0x3a9cc`<br>model `0x11` cmd `0x0b` → `0x47104`<br>`divoom_blue_config_set_blue_name` |
| `0x76` | docs: Get device name | none [app] | length, name [repo] |  | `get_name` | no | `0x3a9e8`<br>model `0x11` cmd `0x0c` → `0x4711c`<br>`divoom_blue_config_get_blue_custom_name`<br>replies `0x76` |
| `0x79` | `SPP_SET_SLEEP_CTRL_MODE` | mode; FF read [app] | mode [app] | sleep control [app] |  | sends, parses | `0x3aa00`<br>model `0x04` cmd `0x03` → `0x4a120`<br>replies `0x79` |
| `0x7c` | `SPP_LED_UPDATE_FONT_INFO` | total length, start, count, {char LE16, 32-byte 16x16 glyph} x count [app] |  | glyphs for native text; no case in model 0x0b, ignored by this firmware [fw]; the device uses its own font |  | sends | `0x3aa1c`<br>model `0x0b` cmd `0x10` → no case: ignored |
| `0x7d` | `SPP_GET_SD_MUSIC_LIST_TOTAL_NUM`<br>docs: Get sd music list total num | none [app] | count LE16 [app] |  | `sd_track_count` | sends, parses | `0x3b0d4`<br>direct<br>replies `0x7d` |
| `0x7e` | `SPP_SET_DIVOOM_LEAVE_MSG_GIF` |  |  | leave-message animation [app] |  | no | `0x3aa38`<br>model `0x0b` cmd `0x0d` → `0x38398`<br>`divoom_app_download_gif_add_data` |
| `0x7f` | `SPP_DEL_LEAVE_MSG_GIF` | none [app] |  | delete leave-message animation [app] |  | sends | `0x3aa6c`<br>model `0x0b` cmd `0x18` → `0x37e7c` |
| `0x82` | `SPP_SET_ALARM_VOICE_CTRL`<br>docs: Set alarm vol ctrl | 2 bytes [app] | 1 byte [app] | alarm voice control [app] |  | sends, parses | `0x3aa84`<br>model `0x0b` cmd `0x1a` → `0x38400`<br>`divoom_alarm_file_manage_del`; `divoom_alarm_file_manage_open_write`; `divoom_alarm_file_manage_write_close`<br>replies `0x00`<br>log `a2dp isn't ready! we will exit!` |
| `0x83` | `SPP_SET_SONG_DIS_CTRL`<br>docs: Set song dis ctrl | 1/0; FF read [app, repo] | 1/0 [app] | song name display [app] | `get_song_display`, `set_song_display` | sends, parses | `0x3aab8`<br>direct<br>replies `0x83` |
| `0x84` | `SPP_RESET_NOTIFICATIONS` | none [app] |  | reset notifications [app] |  | sends | `0x3aae8`<br>model `0x12` cmd `0x00` → `0x372ec` |
| `0x85` | `SPP_SEND_HOTCTRL` | 2 bytes [app] |  | no case in model 0x11, ignored by this firmware [fw] |  | sends | `0x3ab1c`<br>model `0x11` cmd `0x0f` → no case: ignored |
| `0x86` | `SPP_SEND_LED_WORD_CMD` | `01 len UTF-16LE text` set text; `00 speed` [app, repo] |  | native scrolling text [repo] | `show_native_text` | sends | `0x3ab38`<br>model `0x09` cmd `0x0e` → `0x434dc` |
| `0x88` | `SPP_SEND_GAME_SHARK`<br>docs: Send game shark | none [app, repo] |  | "go" in games [app] | `game_shake` | sends | `0x3adc8`<br>model `0x08` cmd `0x01` → `0x38936` |
| `0x8a` | `SPP_SET_POWER_CHANNEL`<br>docs: Set poweron channel | `01 channel` set; `00` read [app, repo] | channel [app] | channel after power on [app] | `get_boot_channel`, `set_boot_channel` | sends, parses | `0x3ab00`<br>model `0x11` cmd `0x1d` → `0x46e08`<br>replies `0x8a` |
| `0x8b` | `SPP_APP_NEW_GIF_CMD2020`<br>docs: App new send gif cmd | 2020 animation transfer [app] | device asks: `00` send all, `01 idx LE16` resend [app] | used by andreas-mausch for uploads |  | parses | `0x39e66`<br>model `0x0b` cmd `0x14` → `0x37eb0`<br>`divoom_app_download_end_cur_file`; `divoom_save_file_init`; `divoom_main_update_user_define_offset`<br>replies `0x48`, `0x8b`, `0x8c`, `0xb1` |
| `0x8c` | `SPP_APP_NEW_USER_DEFINE2020`<br>docs: App new user define | `00, total length LE32, page` starts a custom page upload; `02` alone [app] | `01 idx` resend [app] |  |  | sends, parses | `0x39e80`<br>model `0x0b` cmd `0x15` → `0x37eb0`<br>`divoom_app_download_end_cur_file`; `divoom_save_file_init`; `divoom_main_update_user_define_offset`<br>replies `0x48`, `0x8b`, `0x8c`, `0xb1` |
| `0x97` | `SPP_GET_FILE_VERSION` | `00` [app] | type, version LE16 [app, repo] | firmware version | `firmware_version` | sends, parses | `0x3ab54`<br>direct<br>`divoom_update_device_send_version`<br>replies `0x97` |
| `0x98` | `SPP_APP_UPDATE_FILE_INFO` | file type, version (LE16; LE32 above 65535), size LE32, byte sum LE32 [app, repo] | type, 0 = ready + start chunk LE16, else not ready [app, repo] | firmware update metadata (`flash_mva.py`) |  | sends, parses | `0x3ab60`<br>model `0x0b` cmd `0x0a` → `0x38290`<br>`divoom_update_device_init`; `divoom_app_download_event`<br>replies `0x98` |
| `0x99` | `SPP_APP_SEND_FILE_DATA` | chunk LE16, 256 bytes [app, repo] | `00 00 idx LE16` resend, `00 01` done, `00 02` failed [app, repo] | firmware update data; the app waits 20 ms per chunk [app] |  | sends, parses | `0x3ac1c`<br>model `0x0b` cmd `0x0c` → `0x38334`<br>replies `0x48` |
| `0x9a` | ? |  | Bluetooth address [fw] | not in the app; `divoom_os_com_btdev_get_addr` [fw] |  | no | `0x3ac64`<br>direct<br>`divoom_os_com_btdev_get_addr`<br>replies `0x9a` |
| `0x9b` | `SPP_SEND_HOT_FILE_LIST` | count, {vendor LE32, newest version LE32} x count [app] | the device answers with 0xf7 [fw, repo] | cloud file list |  | sends | `0x3ac80`<br>model `0x0b` cmd `0x08` → `0x38240`<br>replies `0xf7` |
| `0x9d` | `SPP_HOT_UPDATE_FILE_INFO` | vendor LE32, size LE32, byte sum LE32, version LE32 [app, repo] | `00 start LE16` [app] | cloud file info |  | sends, parses | `0x3acc0`<br>model `0x0b` cmd `0x09` → `0x38190`<br>`divoom_screen_save_init`<br>replies `0x9d` |
| `0x9e` | `SPP_HOT_SEND_FILE_DATA` | chunk LE16, 256 bytes [app, repo] | `00 idx LE16` resend, `01`/`02` done [app] | cloud file data |  | sends, parses | `0x3ad20`<br>model `0x0b` cmd `0x06` → `0x38254`<br>replies `0x9f` |
| `0x9f` | `SPP_HOT_PAUSE_FILE_SEND` | none [app] | the device can send it to pause [app] | cloud transfer pause |  | sends, parses | `0x3ad68`<br>model `0x0b` cmd `0x07` → `0x38234`<br>`divoom_app_download_end_cur_file`<br>replies `0x48` |
| `0xa0` | `SPP_SET_GAME`<br>docs: Set game | `01 id` start, `00 00` exit [app, repo] |  | 15 games for the Ditoo Pro (GameTypeV5) [app] | `game` | sends | `0x3adf4`<br>model `0x08` cmd `0x03` → `0x38904`<br>`divoom_app_schedule_cancel`<br>replies `0xa0` |
| `0xa1` | `SPP_SET_TALK` | on, value [app] |  | "Talking Faces" in fhem-Divoom (on, type); the app's Voice page sends it [app, timebox] |  | sends | `0x3ae40`<br>model `0x0a` cmd `0x06` → `0x44500`<br>`divoom_app_schedule_cancel`; `divoom_app_schedule_push` |
| `0xa2` | `SPP_GET_SCENE`<br>docs: Get sleep scene | none [app] | minutes, mode, on, FM (as 0x43), volume, R G B, light [app, repo] |  | `get_sleep` | sends, parses | `0x3ae64`<br>model `0x04` cmd `0x02` → `0x4a144`<br>replies `0xa2` |
| `0xa3` | `SPP_SET_SCENE_LISTEN`<br>docs: Set sleep scene listen | on, a, b [app] |  | sleep sound preview [app] |  | sends | `0x3ae7c`<br>model `0x04` cmd `0x07` → `0x4a204` |
| `0xa4` | `SPP_SET_SCENE_LISTEN_VOLUME`<br>docs: Set scene vol | volume [app] |  |  |  | sends | `0x3ae98`<br>model `0x04` cmd `0x06` → `0x4a1f8` |
| `0xa5` | `SPP_SET_ALARM_LISTEN`<br>docs: Set alarm listen | on, mode, volume [app, repo] |  | alarm preview [app] | `alarm_preview` | sends | `0x3aeb4`<br>direct |
| `0xa6` | `SPP_SET_ALARM_LISTEN_VOLUME`<br>docs: Set alarm vol | volume [app] |  |  |  | sends | `0x3aec4`<br>direct |
| `0xa7` | `SPP_SET_SOUND_CTRL`<br>docs: Set sound ctrl | 1/0 [app, repo] |  | no effect on this device [repo] | `set_sound_controls_screen` | sends | `0x3aed0`<br>model `0x11` cmd `0x08` → `0x472b0`<br>`divoom_screen_bright`<br>replies `0xa8` |
| `0xa8` | `SPP_GET_SOUND_CTRL`<br>docs: Get sound ctrl | none [app] | 1/0 [app] |  | `get_sound_controls_screen` | sends, parses | `0x3aeec`<br>model `0x11` cmd `0x08` → `0x472b0`<br>`divoom_screen_bright`<br>replies `0xa8` |
| `0xa9` | `SPP_SET_PLAY_STOP_VOICE` | 1 byte [app] |  | play/stop recorded voice [app] |  | sends | `0x3af04`<br>model `0x0a` cmd `0x05` → `0x44688` |
| `0xaa` | `SPP_GET_PLAY_VOICE_STATUS` | none [app] | status [app] |  |  | sends, parses | `0x3af20`<br>model `0x0b` cmd `0x1b` → `0x383b8`<br>`divoom_alarm_file_manage_check_room`<br>replies `0xaa` |
| `0xab` | `SPP_SET_AUTO_POWER_OFF`<br>docs: Set auto power off | minutes LE16 [app, repo] |  | auto power off | `set_auto_power_off` | sends | `0x3af38`<br>model `0x11` cmd `0x0a` → `0x47318`<br>replies `0xac` |
| `0xac` | `SPP_GET_AUTO_POWER_OFF`<br>docs: Get auto power off | none [app] | minutes LE16 [app] |  | `get_auto_power_off` | sends, parses | `0x3af54`<br>model `0x11` cmd `0x0a` → `0x47318`<br>replies `0xac` |
| `0xad` | `SPP_SET_SLEEP_COLOR`<br>docs: Set sleep color | R G B [app, repo] |  | sleep colour | `set_sleep_color` | sends | `0x3af6c`<br>model `0x04` cmd `0x04` → `0x4a1e2` |
| `0xae` | `SPP_SET_SLEEP_LIGHT`<br>docs: Set sleep light | brightness [app, repo] |  | sleep light | `set_sleep_light` | sends | `0x3af84`<br>model `0x04` cmd `0x05` → `0x4a1ec` |
| `0xaf` | `SPP_SET_CONNECTED_FLAG` | 1 byte [app] |  | connected flag |  | sends | `0x3afa0`<br>model `0x11` cmd `0x0d` → `0x47370`<br>`divoom_config_read`; `divoom_config_write`; `divoom_main_event`<br>replies `0xb0` |
| `0xb0` | `SPP_GET_CONNECTED_FLAG` | none [app] | flag, product [app] | on the reply the app posts region and device name to `PostRegionId` / `UserProduct` [app] |  | sends, parses | `0x3afbc`<br>model `0x11` cmd `0x0d` → `0x47370`<br>`divoom_config_read`; `divoom_config_write`; `divoom_main_event`<br>replies `0xb0` |
| `0xb1` | `SPP_SET_USER_GIF`<br>docs: Set user gif | user animation [app] | event [app] |  |  | sends, parses | `0x3afd4`<br>model `0x0b` cmd `0x01` → `0x38074`<br>`divoom_main_update_user_define_offset`; `divoom_app_download_end_cur_file`<br>replies `0x48`, `0xb1` |
| `0xb2` | `SPP_SET_ENERGY_CTRL`<br>docs: Set low power switch | 1/0 [app, repo] |  | low power | `set_low_power` | sends | `0x3aff4`<br>model `0x11` cmd `0x09` → `0x472e8`<br>replies `0xb3` |
| `0xb3` | `SPP_GET_ENERGY_CTRL`<br>docs: Get low power switch | none [app] | 1/0 [app] |  | `get_low_power` | sends, parses | `0x3b010`<br>model `0x11` cmd `0x09` → `0x472e8`<br>replies `0xb3` |
| `0xb4` | `SPP_GET_SD_MUSIC_INFO`<br>docs: Get sd music info | none [app] | SD track info [app] |  | `sd_music_info` | sends, parses | `0x3b16c`<br>direct<br>replies `0xb4` |
| `0xb5` | docs: Set sd music info |  |  | SD track info set [docs] |  | no | `0x3b1da`<br>direct |
| `0xb6` | `SPP_MODIFY_RHYTHM_ITEMS`<br>docs: Modify user gif items | index; FF read [app] | index [app] | rhythm animation items [app] |  | sends, parses | `0x3b028`<br>model `0x11` cmd `0x17` → `0x47454`<br>`divoom_main_event`<br>replies `0xb6` |
| `0xb7` | `SPP_SET_RHYTHM_GIF`<br>docs: Set rhythm gif |  |  | rhythm animation [app] |  | no | `0x3b044`<br>model `0x0b` cmd `0x0d` → `0x38398`<br>`divoom_app_download_gif_add_data` |
| `0xb8` | `SPP_SET_SD_MUSIC_POSITION`<br>docs: Set sd music postion | seconds LE16 [app, repo] |  | SD seek | `sd_seek` | sends | `0x3b118`<br>direct |
| `0xb9` | `SPP_SET_SD_MUSIC_PLAY_MODE`<br>docs: Set sd music play mode | mode [app, repo] |  | SD play mode | `sd_play_mode` | sends | `0x3b14c`<br>direct |
| `0xba` | ? | data[1] picks the command: 0 -> 0x0f, else 5 [fw] | 0xf7 [fw] | not in the app; download model 0x0b [fw] |  | no | `0x3b284`<br>model `0x0b` cmd `0x0f` → `0x3851c`<br>model `0x0b` cmd `0x05` → `0x38512`<br>replies `0xf7` |
| `0xbb` | `SPP_SET_POWERON_VOICE_VOL`<br>docs: Set poweron voice vol | `01 volume` set; `00 00` read [app, repo] | volume [app] | power-on voice volume | `get_boot_volume`, `set_boot_volume` | sends, parses | `0x3a2cc`<br>direct<br>replies `0xbb` |
| `0xbc` | `SPP_SET_NEW_MIX_MUSIC_MODE` | mode, value; `mode, FF` read [app] |  | new mixer mode [app] |  | sends | `0x3b2a0`<br>model `0x09` cmd `0x0b` → `0x4340c` |
| `0xbd` | `SPP_DIVOOM_EXTERN_CMD` | sub-command, data; see the 0xBD table [app] | sub-command, data [app] | extended commands | `show_custom` | sends, parses | `0x39eb0`<br>second table at `0x39ed8` on data[1], see below |
| `0xf9` | ? |  |  | not in the app [fw] |  | no | `0x39dbc`<br>direct |
| `0xfb` | ? |  |  | not in the app; logs "update and power off" [fw] |  | no | `0x39e9c`<br>direct |

## 0xBD extended commands

The 0xBD handler logs `extern cmd:0x%x` and jumps through a second table at `0x39ed8` on data[1] (`0x10`..`0x20`). The packet is `BD, sub, data`.

| Sub | Name | Data | Reply | Notes | App | Firmware |
|---|---|---|---|---|---|---|
| `0x10` | `SppExtCarMode` | `00` read, `01 on` [app] | 1/0 [app] | car mode | sends, parses | `0x3a0f4`<br>model `0x11` cmd `0x1f` → `0x46dc4`<br>replies `0xbd` |
| `0x11` | `SPP_SECOND_SET_KEY_PIC` |  |  | set key picture; ignored by this firmware | no | ignored |
| `0x12` | `SPP_SECOND_SET_KEY_FUNC` | `00` / `02` [app] | 12 key function bytes [app] | key functions; ignored by this firmware | sends, parses | ignored |
| `0x13` | `SPP_SECOND_SET_GIF_TYPE` | none [app] | 2020 animation time LE16 at +2 [app] | logs `send SPP_SECOND_SET_GIF_TYPE!` [fw] | sends, parses | `0x3a144`<br>direct<br>replies `0xbd`<br>log `send SPP_SECOND_SET_GIF_TYPE!` |
| `0x14` | `SPP_SECOND_SET_USER_DEFINE_TIME` | seconds LE16 [app] |  | custom page interval | sends | `0x3a020`<br>model `0x11` cmd `0x22` → `0x46d10`<br>replies `0xbd` |
| `0x15` | `SPP_SECOND_GET_USER_DEFINE_TIME` | none [app] | seconds LE16 [app] |  | sends, parses | `0x3a03c`<br>model `0x11` cmd `0x22` → `0x46d10`<br>replies `0xbd` |
| `0x16` | `SPP_SECOND_CLEAR_USER_DEFINE_INDEX` | page [app] |  | clear a custom page | sends | `0x3a058`<br>model `0x11` cmd `0x24` → `0x46cb0`<br>model `0x0b` cmd `0x17` → `0x37e88`<br>replies `0x31` |
| `0x17` | `SPP_SECOND_USE_USER_DEFINE_INDEX` | page 0..2, FF read [app, repo] | current page [app] | select a custom page after `45 05` (`client.show_custom`, `homeassistant/`, hass-divoom `show_design`) | sends, parses | `0x3a0d8`<br>model `0x11` cmd `0x23` → `0x46cdc`<br>replies `0x31`, `0xbd` |
| `0x18` | `SPP_SECOND_GET_NEW_POWER_ON_CHANNEL` | none [app] | 1 = supports the new power-on channel [app] |  | sends, parses | `0x3a0b8`<br>direct<br>replies `0xbd` |
| `0x19` | `SPP_SECOND_SET_SAVE_VOLUME_CFG` | 1/0, FF read [app] | 1/0 [app] | remember volume | sends, parses | `0x3a084`<br>direct<br>replies `0xbd` |
| `0x1a` | `SPP_SECOND_SET_AUTO_CONNECT_CFG` | 1/0, FF read [app] | 1/0 [app] | reconnect automatically | sends, parses | `0x39f8c`<br>direct<br>replies `0xbd` |
| `0x1b` | `SPP_SECOND_SET_GIF_PLAY_TIME_CFG` | seconds LE16, FFFF read [app] | seconds LE16 [app] | animation play time; calls `divoom_main_set_play_gif_time` [fw] | sends, parses | `0x39fe0`<br>direct<br>`divoom_main_set_play_gif_time`<br>replies `0xbd` |
| `0x1c` | `SPP_SECOND_SET_MUSIC_NAME_CFG` | `00` read, `01 a b c d` [app] | 4 bytes [app] | song name display settings | sends, parses | `0x39f38`<br>model `0x11` cmd `0x30` → `0x467d0`<br>replies `0x31`, `0xbd` |
| `0x1d` | `SPP_SECOND_RECORD_CTRL` | 1 byte [app] | 1 byte [app] | recording | sends, parses | `0x39f70`<br>model `0x11` cmd `0x33` → `0x46a84` |
| `0x1e` | `SPP_SECOND_KARAOKE_CTRL` | `00` read [app] | 4 bytes [app] | karaoke; logs `app get  karaoke_para!` [fw] | sends, parses | `0x39f54`<br>model `0x11` cmd `0x31` → `0x4686c`<br>replies `0xbd`<br>log `app get  karaoke_para!` |
| `0x1f` | ? |  |  | not in the app [fw] | no | `0x3a110`<br>direct<br>replies `0xbd` |
| `0x20` | `SPP_SECOND_WIRELESS_MIC_CTRL` | `00` read [app] | 1 byte [app] | wireless microphone | sends, parses | `0x39f1c`<br>model `0x11` cmd `0x38` → `0x46624`<br>replies `0xbd` |

The app also knows sub-commands this firmware does not reach (the table ends at `0x20`): `0x21` SPP_SECOND_TOUCH, `0x23` SPP_SECOND_SET_SCREEN_DIR_CFG, `0x24` SPP_SECOND_SET_SCREEN_MIRROR_CFG, `0x25` SPP_SECOND_CLEAR_SYS_CFG, `0x26` SPP_SECOND_SET_LANGUAGE, `0x27` SPP_SECOND_SUPPORT_MORE_ANCS, `0x28` SPP_SECOND_GET_DECODE_FILE_INFO, `0x29` SPP_SECOND_GET_NORMAL_FILE_INFO, `0x2a` SPP_SECOND_GET_CLOCK_ID_INFO, `0x2b` SPP_SECOND_GET_DEVICE_INFO, `0x2c` SPP_SECOND_DEVICE_FORMAT_FLAG, `0x2d` SPP_SECOND_GET_FONT_INFO, `0x2e` SPP_GET_SYSTEM_TIME, `0x2f` SPP_SECOND_OPEN_SCREEN_CTRL, `0x30` SPP_SECOND_GET_ZSTD_DECODE_FILE_INFO, `0x31` SPP_SECOND_APP_SEND_GIF_START, `0x32` SPP_SECOND_SEND_DEVICE_EQUALIZER_CTRL, `0x33` SPP_SECOND_SEND_DEVICE_LIGHT_EFFECT_CTRL, `0x34` SPP_SECOND_APP_SET_VOL, `0x35` SPP_SECOND_APP_GET_VOL, `0x36` SPP_SECOND_APP_SET_MUSIC_PLAY_PAUSE, `0x37` SPP_SECOND_GET_EZIP_DECODE_FILE_INFO, `0x38` SPP_SECOND_GET_EZIP_ROTATE_DECODE_FILE_INFO, `0x3a` SPP_SECOND_GET_JPEG_PIXEL_FILE_INFO. The device sends BD 2E (`SPP_GET_SYSTEM_TIME`) to ask for the time; the app answers with 0x18.

## 0x45 channel payloads

First data byte = channel (the app's `CmdManager.LightEnum`):

| Channel | Data |
|---|---|
| 0 clock | `00, 24h, style, 01, weather, temperature, calendar, R, G, B` (hass-divoom, `client.show_clock`) |
| 1 light | `01, R, G, B, brightness, effect, on, 00, 00, 00` |
| 2 cloud | `02` |
| 3 special effect | `03, effect` |
| 4 music | `04, mode` (padded to 10 bytes by the app) |
| 5 custom | `05`, then `BD 17 <page>` selects page 0..2 |
| 6 lyrics | `06, 00, 00, 00` (hass-divoom) |

## 0x46 state reply

Field names from the app's `LightCache` table; `client.LightState` has the same layout.

| Offset | Field | Offset | Field |
|---|---|---|---|
| 0 | channel (`mode`) | 10 | system brightness (`sys_light`) |
| 1 | Fahrenheit (`temp_type`) | 11 | 24-hour (`time_type`) |
| 2 | VJ effect (`vj_type`) | 12–14 | clock R, G, B |
| 3–5 | light R, G, B | 15 | clock style (`time_show_mode`) |
| 6 | light brightness (`light_level`) | 16–19 | clock flags (`time_check`) |
| 7 | light effect (`light_mode`) | | |
| 8 | light on (`light_on_off`) | | |
| 9 | music mode (`music_type`) | | |

## Models

Table at `0x177728`. Name: the function-name string the model function logs, else the most common `divoom_<word>` prefix of the functions its cases call.

Area: the Divoom doc category most of its opcodes belong to. Models without commands are not reached from BLE commands (other firmware code posts to them).

| Model | Function | Name | Area | Commands used (opcode → cmd) |
|---|---|---|---|---|
| `0x03` | `0x36a28` | (divoom_alarm_spp…) | alarm memorial | 43→00, 42→01, 54→02, 53→03 |
| `0x04` | `0x4a0e8` |  | sleep | 41→00, 40→01, a2→02, 79→03, ad→04, ae→05, a4→06, a3→07 |
| `0x05` | `0x3c58c` | divoom_app_tool_event |  |  |
| `0x06` | `0x4ad00` | divoom_time_plan_event | time plan | 56→00, 57→01, 57→02 |
| `0x08` | `0x388b8` | divoom_app_game_play_event | game | 88→01, 17→02, a0→03 |
| `0x09` | `0x42eb0` | divoom_draw_event | light | 58→00, 44→01, 5a→02, 16→05, 35→09, 6a→0a, bc→0b, 6e→0c, 6c→0d, 86→0e, 5b→0f, 3a→10, 6f→12 |
| `0x0a` | `0x444ac` | (divoom_app_schedule…) |  | a9→05, a1→06 |
| `0x0b` | `0x37dd8` | (divoom_app_download…) | light | b1→01, ba→05, 9e→06, 9f→07, 9b→08, 9d→09, 98→0a, 48→0b, 99→0c, 7e→0d, b7→0d, 1b→0d, 49→0d, 51→0d, 52→0d, 55→0d, 1a→0e, ba→0f, 7c→10 (ignored), 8b→14, 8c→15, BD 16→17, 7f→18, 82→1a, 1c→1a, aa→1b, 3c→1c |
| `0x0f` | `0x3b2c0` |  |  |  |
| `0x11` | `0x464b0` | divoom_main_event | system setting | 46→01, 26→03, 74→04, 31→04, 32→04, 2d→06, 2b→07, a7→08, a8→08, b2→09, b3→09, ab→0a, ac→0a, 75→0b, 76→0c, af→0d, b0→0d, 85→0f (ignored), b6→17, 27→1b, 8a→1d, BD 10→1f, BD 14→22, BD 15→22, BD 17→23, BD 16→24, 23→2f, BD 1c→30, BD 1e→31, BD 1d→33, BD 20→38 |
| `0x12` | `0x37260` | (divoom_ancs_start…) |  | 84→00, 4a→01, 4b→02, 50→05 |
| `0x14` | `0x4bab8` | (divoom_weather_net…) | system setting | 5f→00, 5d→01, 5e→02, 73→03 |
| `0x15` | `0x48988` |  |  |  |
| `0x16` | `0x38d78` | divoom_app_menu_event |  |  |
| `0x17` | `0x37410` | divoom_app_copy_draw_event |  |  |
| `0x18` | `0x43ba8` |  |  |  |

## Handled by the dispatcher but doing nothing

`0x7c` SPP_LED_UPDATE_FONT_INFO, `0x85` SPP_SEND_HOTCTRL

## Neither in the app nor in Divoom's docs

Handled by this firmware, but no known host sends them (factory or test commands?): `0x10`, `0x4a`, `0x4b`, `0x9a`, `0xba`, `0xf9`, `0xfb`.

## Documented by Divoom but ignored by this firmware

`0x14` Send sd list over, `0x2c` Set hour type, `0x34` Sand paint ctrl, `0x3b` Drawing big pad ctrl, `0x59` Get device temp, `0x5c` Drawing mul encode  pic, `0x6b` Drawing mul encode  gif play, `0x6d` Drawing mul encode movie play, `0x87` Set light phone word attr, `0x8d` App big64 user define, `0x8e` App get user define info

## In the Android app but ignored by this firmware

`0x01` SPP_JSON, `0x14` SPP_SEND_SD_LIST_OVER, `0x19` SPP_DEVICE_UPDATE_GIF, `0x1d` SPP_LIEGHT_SET_25DOTS_PIC, `0x1e` SPP_DEVICE_TO_APP_GIF, `0x20` SPP_SPP_SET_MIC_SWITCH, `0x22` SPP_EYE_GUARD_INFO, `0x24` SPP_SYSTEM_TRY_COLOR, `0x25` SPP_SEND_APP_NEW_PIC_INFO, `0x34` SPP_SAND_PAINT_CTRL, `0x36` SPP_GET_FILE_VERSION2, `0x37` SPP_GET_FILE_VERSION2_LIST, `0x3b` SPP_DRAWING_BIG_PAD_CTRL, `0x3e` SPP_MOVE_RESET_IFRAME, `0x59` SPP_GET_DEVICE_TEMP_INFO, `0x5c` SPP_DRAWING_ENCODE_PLAY, `0x60` SPP_GET_FM_CURRENT_FREQ, `0x61` SPP_SET_FM_CURRENT_FREQ, `0x63` SPP_SET_FM_AUTOMATIC_SEARCH, `0x64` SPP_GET_FM_COUNT_OR_FREQ, `0x67` SPP_SET_FM_FAVOURITE, `0x68` SPP_GET_FM_AUTOMATIC_SEARCH_STATUS, `0x69` SPP_SET_FM_REGION, `0x6b` SPP_DRAWING_MUL_ENCODE_GIF_PLAY, `0x6d` SPP_DRAWING_MUL_ENCODE_MOVIE_PLAY, `0x70` SPP_GET_FM_REGION, `0x77` SPP_SET_MUL_DEVICE_CTRL, `0x87` SPP_LIEGHT_PHONE_GIF32_WORD_ATTR, `0x89` SPP_SET_PERIPHERALS_DEVICE_CTRL, `0x8d` SPP_APP_BIG64_USER_DEFINE, `0x8e` SPP_APP_GET_USER_DEFINE_INFO, `0x8f` SPP_LOCAL_PICTURE, `0x93` SPP_SYS_DEVICE_UPDATE, `0x94` SPP_SYS_UPDATE_DATA, `0x95` SPP_SYS_CONTINUE_UPDATE_DATA, `0x96` SPP_SYS_GET_UPDATE_ADDR, `0xbe` SPP_SECOND_SEND_SERVER_FILE_INFO, `0xf1` EXIT_BOND_BLE_COMMAND, `0xf2` WIFI_CONFIG_BLE_COMMAND, `0xf3` WIFI_STATUS_BLE_COMMAND, `0xf4` SPP_WIFI_SERVER_TYPE_COMMAND

## Device to app

Opcodes the app parses that are not commands to this firmware (replies or device messages); bold: this firmware sends it. 0xF0 is `START_BOND_BLE_COMMAND` in the app but never sent by it, so the remote mode cannot collide with the app.

`0x01` SPP_JSON, **`0x04`** SPP_COMMAND_CHECK, **`0x14`** SPP_SEND_SD_LIST_OVER, `0x19` SPP_DEVICE_UPDATE_GIF, `0x1d` SPP_LIEGHT_SET_25DOTS_PIC, `0x1e` SPP_DEVICE_TO_APP_GIF, `0x20` SPP_SPP_SET_MIC_SWITCH, `0x22` SPP_EYE_GUARD_INFO, `0x25` SPP_SEND_APP_NEW_PIC_INFO, `0x36` SPP_GET_FILE_VERSION2, `0x37` SPP_GET_FILE_VERSION2_LIST, `0x3b` SPP_DRAWING_BIG_PAD_CTRL, `0x3e` SPP_MOVE_RESET_IFRAME, `0x59` SPP_GET_DEVICE_TEMP_INFO, `0x60` SPP_GET_FM_CURRENT_FREQ, `0x64` SPP_GET_FM_COUNT_OR_FREQ, `0x68` SPP_GET_FM_AUTOMATIC_SEARCH_STATUS, `0x70` SPP_GET_FM_REGION, `0x77` SPP_SET_MUL_DEVICE_CTRL, `0x89` SPP_SET_PERIPHERALS_DEVICE_CTRL, `0x8d` SPP_APP_BIG64_USER_DEFINE, `0x8e` SPP_APP_GET_USER_DEFINE_INFO, `0x8f` SPP_LOCAL_PICTURE, `0xbe` SPP_SECOND_SEND_SERVER_FILE_INFO, `0xf3` WIFI_STATUS_BLE_COMMAND, **`0xf7`** SPP_REQUEST_NEW_FILE_INFO

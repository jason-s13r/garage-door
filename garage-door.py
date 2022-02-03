#!/usr/bin/python3
import logging
from logging.handlers import TimedRotatingFileHandler
import os
import asyncio
import RPi.GPIO as GPIO
import reolinkapi
from concurrent.futures import ProcessPoolExecutor
from telethon import TelegramClient, Button, events, functions, types


logging.basicConfig(format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(
    TimedRotatingFileHandler("logs/garage_door.log", when="midnight", encoding="utf-8")
)

from secrets import *

GARAGE_DOOR = 21

KEYBOARD = [
    [
        Button.text("📷\nGarage"),
        Button.text("📷\nDriveway"),
        Button.text("📷\nShed"),
        Button.text("📷\nPaddock"),
    ],
    [Button.text("↕️ Garage")],
]

# KEYBOARD = [
#     [
#         Button.inline("📷 Garage", b"snap_garage"),
#         Button.inline("📷 Driveway", b"snap_driveway"),
#     ],
#     [
#         Button.inline("📷 Shed", b"snap_shed"),
#         Button.inline("📷 Paddock", b"snap_paddock"),
#     ],
#     [Button.inline("↕️ Garage", b"toggle")],
# ]


async def toggle_relay(relay):
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(relay, GPIO.OUT)
    GPIO.output(relay, True)
    await asyncio.sleep(0.1)
    GPIO.output(relay, False)
    await asyncio.sleep(0.1)
    GPIO.cleanup()


async def save_video(duration=15, filename="capture.mp4"):
    rtmp_url = f"rtmp://{CAMERA_URL}/bcs/channel0_ext.bcs?channel=0&stream=3&user={CAMERA_USER}&password={CAMERA_PASS}"
    await capture_video_async(rtmp_url, filename, duration)


async def take_photo(filename="snap.png"):
    rtmp_url = f"rtmp://{CAMERA_URL}/bcs/channel0_ext.bcs?channel=0&stream=3&user={CAMERA_USER}&password={CAMERA_PASS}"
    await capture_frame_async(rtmp_url, filename)


def capture_video(input, output, duration=10):
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "tcp"
    return os.popen(f'ffmpeg -y -i "{input}" -t {duration} "{output}"').read()


async def capture_video_async(input, output, duration=10, loop=None):
    loop = loop if loop is not None else asyncio.get_event_loop()
    stdout = await loop.run_in_executor(
        ProcessPoolExecutor(), capture_video, input, output, duration
    )
    await asyncio.sleep(0.01)


def capture_frame(input, output):
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "tcp"
    os.popen(f'ffmpeg -y -i "{input}" -t 0.1 "{output}.mp4"').read()
    os.popen(
        f'ffmpeg -y -i "{output}.mp4" -vf "select=eq(n\,0)" -q:v 3 "{output}"'
    ).read()


async def capture_frame_async(input, output, loop=None):
    loop = loop if loop is not None else asyncio.get_event_loop()
    stdout = await loop.run_in_executor(
        ProcessPoolExecutor(), capture_frame, input, output
    )
    await asyncio.sleep(0.01)


def combine_audio_video(audio, video, output):
    os.popen(
        f'ffmpeg -y -i "{video}" -i "{audio}" -c copy -map 0:v:0 -map 1:a:0 "{output}"'
    ).read()


async def combine_audio_video_async(audio, video, output, loop=None):
    loop = loop if loop is not None else asyncio.get_event_loop()
    stdout = await loop.run_in_executor(
        ProcessPoolExecutor(), combine_audio_video, audio, video, output
    )
    await asyncio.sleep(0.01)


with TelegramClient("bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN) as bot:

    @bot.on(events.NewMessage)
    async def bouncer(event):
        if event.sender_id in (JASON, CHELSEA):
            return
        await event.respond("Forbidden.")
        user = await bot.get_entity(event.sender_id)
        try:
            logger.info(
                f"[FORBIDDEN] by id={user.id}; {user.first_name} {user.last_name}."
            )
        except:
            logger.info(
                f"[FORBIDDEN] id={event.sender_id}; non-user has been forbidden."
            )
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern="/start"))
    async def start_handler(event):
        user = await bot.get_entity(event.sender_id)
        await event.respond(
            f"Take a photo or toggle the garage door.", buttons=KEYBOARD
        )
        logger.info(f"[START] by id={user.id}; {user.first_name} {user.last_name}.")
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern="↕️ Garage"))
    @bot.on(events.NewMessage(pattern="/toggle"))
    @bot.on(events.CallbackQuery(data=b"toggle"))
    async def toggle_handler(event):
        upload = None
        await bot(
            functions.messages.SetTypingRequest(
                peer=event.sender_id, action=types.SendMessageTypingAction()
            )
        )
        user = await bot.get_entity(event.sender_id)
        name = f"[{user.first_name}](tg://user?id={user.id})"
        message = f"Garage Door toggled by {name}."
        camera = reolinkapi.Camera(CAMERA_URL, CAMERA_USER, CAMERA_PASS)

        try:
            camera.go_to_preset(index=PRESET_GARAGE)
        except:
            pass

        try:
            await asyncio.gather(
                *[
                    toggle_relay(GARAGE_DOOR),
                    save_video(12),
                ]
            )

            await bot(
                functions.messages.SetTypingRequest(
                    peer=event.sender_id, action=types.SendMessageTypingAction()
                )
            )
            upload = await bot.upload_file(file="capture.mp4")
        except Exception as error:
            logger.error(error)

        await bot(
            functions.messages.SetTypingRequest(
                peer=event.sender_id, action=types.SendMessageCancelAction()
            )
        )
        recipients = list(set([JASON, CHELSEA, event.sender_id]))
        for recipient in recipients:
            if recipient == event.sender_id:
                await event.reply(message, file=upload, buttons=KEYBOARD)
            else:
                await bot.send_message(
                    recipient, message, file=upload, buttons=KEYBOARD
                )
        logger.info(f"[TOGGLE] {message}")

        await asyncio.sleep(1)
        camera.go_to_preset(index=PRESET_SHED)
        if os.path.exists("capture.mp4"):
            os.remove("capture.mp4")

        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r"\/snap_(garage|shed|driveway|paddock)"))
    async def snap_handler(event):
        where = event.text.strip()[6:]
        await snap_response(event, where)
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r"(?i)^📷\n(garage|shed|driveway|paddock)"))
    async def snap_handler(event):
        where = event.text.lower().replace("📷\n", "").strip()
        await snap_response(event, where)
        raise events.StopPropagation

    @bot.on(events.CallbackQuery(data=b"snap_garage"))
    @bot.on(events.CallbackQuery(data=b"snap_driveway"))
    @bot.on(events.CallbackQuery(data=b"snap_shed"))
    @bot.on(events.CallbackQuery(data=b"snap_paddock"))
    async def snap_callback_handler(event):
        where = "shed"
        if event.data == b"snap_garage":
            where = "garage"
        elif event.data == b"snap_driveway":
            where = "driveway"
        elif event.data == b"snap_paddock":
            where = "paddock"
        await snap_response(event, where)
        raise events.StopPropagation

    async def snap_response(event, where):
        presets = dict(
            garage=PRESET_GARAGE,
            driveway=PRESET_DRIVEWAY,
            shed=PRESET_SHED,
            paddock=PRESET_PADDOCK,
        )
        filename = f"snap_{where}.png"
        logger.info(f"[SNAP] {where}")
        await bot(
            functions.messages.SetTypingRequest(
                peer=event.sender_id, action=types.SendMessageTypingAction()
            )
        )
        try:
            camera = reolinkapi.Camera(CAMERA_URL, CAMERA_USER, CAMERA_PASS)
            camera.go_to_preset(index=presets[where])
            await asyncio.sleep(1)
            await take_photo(filename)
            await event.reply(file=filename, buttons=KEYBOARD)
        except:
            await event.reply("error", buttons=KEYBOARD)

        await asyncio.sleep(1)
        camera.go_to_preset(index=PRESET_SHED)
        await bot(
            functions.messages.SetTypingRequest(
                peer=event.sender_id, action=types.SendMessageCancelAction()
            )
        )
        if os.path.exists(filename):
            os.remove(filename)

    @bot.on(events.NewMessage(pattern=r"\/video_(garage|shed|driveway|paddock)"))
    async def video_handler(event):
        where = event.text.strip()[7:]
        await video_response(event, where)
        raise events.StopPropagation

    async def video_response(event, where, duration=10):
        presets = dict(
            garage=PRESET_GARAGE,
            driveway=PRESET_DRIVEWAY,
            shed=PRESET_SHED,
            paddock=PRESET_PADDOCK,
        )
        filename = f"video_{where}.mp4"
        logger.info(f"[VIDEO] {where}")
        await bot(
            functions.messages.SetTypingRequest(
                peer=event.sender_id, action=types.SendMessageTypingAction()
            )
        )
        try:
            camera = reolinkapi.Camera(CAMERA_URL, CAMERA_USER, CAMERA_PASS)
            camera.go_to_preset(index=presets[where])
            await asyncio.sleep(1)
            await save_video(duration, filename)
            await event.reply(file=filename, buttons=KEYBOARD)
        except:
            await event.reply("error", buttons=KEYBOARD)

        await asyncio.sleep(1)
        camera.go_to_preset(index=PRESET_SHED)
        await bot(
            functions.messages.SetTypingRequest(
                peer=event.sender_id, action=types.SendMessageCancelAction()
            )
        )
        if os.path.exists(filename):
            os.remove(filename)


    @bot.on(events.NewMessage(pattern=r"/(spin|konami|↑↑↓↓←→←→BA|⬆⬆⬇⬇⬅➡⬅➡🅱🅰)"))
    async def spin_handler(event):
        logger.info(f"[SPIN]")
        await bot(
            functions.messages.SetTypingRequest(
                peer=event.sender_id, action=types.SendMessageTypingAction()
            )
        )
        filename = f"spin.mp4"
        try:
            camera = reolinkapi.Camera(CAMERA_URL, CAMERA_USER, CAMERA_PASS)
            camera.auto_movement()
            await save_video(15, filename)
            if "spin" not in event.text:
                await combine_audio_video_async("konami.m4a", filename, "konami.mp4")
                filename = "konami.mp4"
            await event.reply(file=filename, buttons=KEYBOARD)
        except:
            await event.reply("error", buttons=KEYBOARD)
        await asyncio.sleep(1)
        camera.go_to_preset(index=PRESET_SHED)
        await bot(
            functions.messages.SetTypingRequest(
                peer=event.sender_id, action=types.SendMessageCancelAction()
            )
        )
        if os.path.exists("spin.mp4"):
            os.remove("spin.mp4")
        if os.path.exists("konami.mp4"):
            os.remove("konami.mp4")
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern="/options"))
    async def handler(event):
        await event.respond("options", buttons=KEYBOARD)

    logger.info("hello!")
    bot.run_until_disconnected()

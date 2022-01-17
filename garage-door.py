#!/usr/bin/python3
import logging
from logging.handlers import TimedRotatingFileHandler
import os
import asyncio
import RPi.GPIO as GPIO
import cv2
import numpy
import itertools
import reolinkapi
from concurrent.futures import ProcessPoolExecutor
from telethon import TelegramClient, events, functions, types
from time import sleep, time
from PIL import Image, ImageDraw


logging.basicConfig(format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(
    TimedRotatingFileHandler("logs/garage_door.log", when="midnight", encoding="utf-8")
)

from secrets import *

GARAGE_DOOR = 21


async def toggle_relay(relay):
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(relay, GPIO.OUT)
    GPIO.output(relay, True)
    await asyncio.sleep(0.1)
    GPIO.output(relay, False)
    await asyncio.sleep(0.1)
    GPIO.cleanup()


# @limit("once every 15 seconds")
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


bot = TelegramClient("bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)

ROSIE = 160088387
TANNER = 79316791


@bot.on(events.NewMessage)
async def bouncer(event):
    if event.sender_id in (JASON, CHELSEA, ROSIE):
        return
    await event.respond("Forbidden.")
    user = await bot.get_entity(event.sender_id)
    try:
        logger.info(f"[FORBIDDEN] by id={user.id}; {user.first_name} {user.last_name}.")
    except:
        logger.info(f"[FORBIDDEN] id={event.sender_id}; non-user has been forbidden.")
    raise events.StopPropagation


@bot.on(events.NewMessage(pattern="/start"))
async def start_handler(event):
    user = await bot.get_entity(event.sender_id)
    await event.respond(f"Use /toggle to open or close the garage door.")
    logger.info(f"[START] by id={user.id}; {user.first_name} {user.last_name}.")
    raise events.StopPropagation


@bot.on(events.NewMessage(pattern="/toggle"))
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
            await event.reply(message, file=upload)
        else:
            await bot.send_message(recipient, message, file=upload)

    await asyncio.sleep(1)
    camera.go_to_preset(index=PRESET_SHED)
    if os.path.exists("capture.mp4"):
        os.remove("capture.mp4")

    raise events.StopPropagation


@bot.on(events.NewMessage(pattern=r"\/snap_(garage|shed|driveway|paddock)"))
async def check_handler(event):
    await bot(
        functions.messages.SetTypingRequest(
            peer=event.sender_id, action=types.SendMessageTypingAction()
        )
    )
    where = event.text.strip()[6:]
    presets = dict(
        garage=PRESET_GARAGE,
        driveway=PRESET_DRIVEWAY,
        shed=PRESET_SHED,
        paddock=PRESET_PADDOCK,
    )
    filename = f"snap_{where}.png"
    try:
        camera = reolinkapi.Camera(CAMERA_URL, CAMERA_USER, CAMERA_PASS)
        camera.go_to_preset(index=presets[where])
        await asyncio.sleep(1)
        await take_photo(filename)
        await event.reply(file=filename)
    except:
        await event.reply("error")
    await asyncio.sleep(1)
    camera.go_to_preset(index=PRESET_SHED)
    await bot(
        functions.messages.SetTypingRequest(
            peer=event.sender_id, action=types.SendMessageCancelAction()
        )
    )
    raise events.StopPropagation


@bot.on(events.NewMessage(pattern=r"/(spin|konami|↑↑↓↓←→←→BA|⬆⬆⬇⬇⬅➡⬅➡🅱🅰)"))
async def spin_handler(event):
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
        await event.reply(file=filename)
    except:
        await event.reply("error")
    await asyncio.sleep(1)
    camera.go_to_preset(index=PRESET_SHED)
    await bot(
        functions.messages.SetTypingRequest(
            peer=event.sender_id, action=types.SendMessageCancelAction()
        )
    )
    raise events.StopPropagation


with bot:
    logger.info("hello!")
    bot.run_until_disconnected()

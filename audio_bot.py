import asyncio
import os
import re

import numpy as np
import pyaudiowpatch as pyaudio
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from dotenv import load_dotenv
from playwright.async_api import async_playwright


load_dotenv()
MEET_PROFILE = ".meet_profile"
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")

def print_transcript(message):
    if not hasattr(message, "channel"):
        return

    transcript = message.channel.alternatives[0].transcript
    if transcript and transcript.strip() and getattr(message, "is_final", True):
        words = getattr(message.channel.alternatives[0], "words", []) or []
        speakers = sorted({getattr(word, "speaker", None) for word in words if getattr(word, "speaker", None) is not None})
        label = f"Speaker {speakers[0]}: " if len(speakers) == 1 else ""
        print(f"🎙️ {label}{transcript}")


def print_deepgram_error(error):
    print(f"❌ Deepgram error: {error}")


async def transcribe_audio(audio_queue):
    deepgram = AsyncDeepgramClient(api_key=DEEPGRAM_API_KEY)
    async with deepgram.listen.v1.connect(
        model="nova-2",
        language="th",
        smart_format="true",
        encoding="linear16",
        sample_rate=48000,
        diarize="true",
    ) as connection:
        connection.on(EventType.OPEN, lambda _: print("✅ เชื่อมต่อ Deepgram แล้ว"))
        connection.on(EventType.MESSAGE, print_transcript)
        connection.on(EventType.ERROR, print_deepgram_error)
        connection.on(EventType.CLOSE, lambda _: print("⚠️ Deepgram connection ปิดลง"))
        listener = asyncio.create_task(connection.start_listening())
        try:
            while True:
                audio = await audio_queue.get()
                if audio is None:
                    break
                await connection.send_media(audio)
        finally:
            await connection.send_close_stream()
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)


async def capture_system_audio(audio_queue, audio_connected):
    audio = pyaudio.PyAudio()
    stream = None
    try:
        wasapi = audio.get_host_api_info_by_type(pyaudio.paWASAPI)
        speaker = audio.get_device_info_by_index(wasapi["defaultOutputDevice"])
        loopback_name = f'{speaker["name"]} [Loopback]'
        loopback = audio.get_device_info_by_name(loopback_name)
        sample_rate = int(loopback["defaultSampleRate"])
        channels = min(int(loopback["maxInputChannels"]), 2)

        print(f"🔊 จับเสียงจาก WASAPI loopback: {loopback['name']}")
        stream = audio.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=sample_rate,
            input=True,
            input_device_index=loopback["index"],
            frames_per_buffer=2048,
        )
        print("🎧 เริ่มจับเสียงระบบแล้ว")

        first_chunk = True
        while True:
            raw_audio = await asyncio.to_thread(
                stream.read,
                2048,
                exception_on_overflow=False,
            )
            if channels > 1:
                samples = np.frombuffer(raw_audio, dtype=np.int16).reshape(-1, channels)
                raw_audio = samples[:, 0].tobytes()

            await audio_queue.put(raw_audio)
            if first_chunk:
                print("🎧 ได้รับเสียงจากระบบแล้ว")
                audio_connected.set()
                first_chunk = False
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        audio.terminate()


async def run_audio_bot(meet_url: str):
    if not DEEPGRAM_API_KEY:
        raise RuntimeError("ไม่พบ DEEPGRAM_API_KEY ใน environment variable")

    audio_queue = asyncio.Queue(maxsize=100)
    audio_connected = asyncio.Event()

    transcription_task = None
    capture_task = None

    try:
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=MEET_PROFILE,
                headless=False,
                args=["--start-maximized", "--use-fake-ui-for-media-stream"],
                no_viewport=True,
                permissions=["microphone", "camera"],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(meet_url)
            await page.wait_for_timeout(5000)

            if "accounts.google.com" in page.url or await page.locator('input[type="email"]').count():
                await page.goto("https://accounts.google.com/")
                await asyncio.to_thread(input, "ล็อกอิน Google แล้วกด Enter: ")
                await page.goto(meet_url)

            await page.keyboard.press("Control+d")
            await page.keyboard.press("Control+e")

            join_name = re.compile(r"ขอเข้าร่วม|เข้าร่วมตอนนี้|เข้าร่วมเลย|ask to join|join now", re.I)
            join_button = page.get_by_role("button", name=join_name).first
            try:
                await join_button.wait_for(state="visible", timeout=30000)
                await join_button.click()
            except Exception:
                pass

            joined = page.locator(
                '[aria-label*="วางสาย"], [aria-label*="ออกจาก"], '
                '[aria-label*="leave" i], [aria-label*="hang up" i], [jsname="CQyl2b"]'
            ).first
            await joined.wait_for(state="visible", timeout=120000)
            print("🎉 เข้าห้องสำเร็จแล้ว")
            capture_task = asyncio.create_task(capture_system_audio(audio_queue, audio_connected))
            await audio_connected.wait()
            transcription_task = asyncio.create_task(transcribe_audio(audio_queue))
            print("🎙️ กำลังรอ transcript จากเสียง Google Meet...")
            await asyncio.Event().wait()
    finally:
        if capture_task is not None:
            capture_task.cancel()
            await asyncio.gather(capture_task, return_exceptions=True)
        if transcription_task is not None:
            await audio_queue.put(None)
            await transcription_task


if __name__ == "__main__":
    try:
        asyncio.run(run_audio_bot("https://meet.google.com/khn-anxh-hvz"))
    except KeyboardInterrupt:
        print("\nจบการทำงาน")
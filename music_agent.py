import vlc
import google.genai as genai
from google.genai import types
from yt_dlp import YoutubeDL
import json
import os
from dotenv import load_dotenv

import speech_recognition as sr

load_dotenv()

# --- VLC PLAYER SETUP ---
vlc_instance = vlc.Instance()
audio_player = vlc_instance.media_player_new()
current_song = "None"

# Playback speed mapping
SPEED_MAP = {
    1: 0.75,
    2: 1.0,
    3: 1.25,
    4: 1.5,
    5: 2.0,
}

def find_working_microphones():
    print("\nScanning microphones...")

    working = []
    for i, name in enumerate(sr.Microphone.list_microphone_names()):
        try:
            with sr.Microphone(device_index=i) as source:
                if source.stream is not None:
                    working.append((i, name))
        except:
            continue

    print("\n🎤 Working microphones:")
    for i, name in working:
        print(f"{i}: {name}")

    return working


def listen_for_voice() -> str:
    recognizer = sr.Recognizer()

    with sr.Microphone(device_index=mic_index) as source:
        print("\n🎤 Listening... (talk now)")

        # Better noise handling
        recognizer.adjust_for_ambient_noise(source, duration=0.8)

        try:
            audio = recognizer.listen(source, timeout=6, phrase_time_limit=8)
        except sr.WaitTimeoutError:
            print("⏳ I didn't hear anything. Try again.")
            return ""

    try:
        text = recognizer.recognize_google(audio)
        print(f"🗣 You said: {text}")
        return text.lower()

    except sr.UnknownValueError:
        print("❗ Sorry, I couldn't understand that.")
        return ""

    except sr.RequestError:
        print("❗ Speech recognition service failed.")
        return ""



# def listen_for_voice() -> str:
#     recognizer = sr.Recognizer()
#     mic = sr.Microphone()

#     print("\n🎤 Say something...")

#     with mic as source:
#         recognizer.adjust_for_ambient_noise(source)
#         audio = recognizer.listen(source)

#     try:
#         text = recognizer.recognize_google(audio)
#         print(f"🗣 You said: {text}")
#         return text.lower()
#     except sr.UnknownValueError:
#         print("❗ I couldn't understand. Please try again.")
#         return ""
#     except sr.RequestError:
#         print("❗ Speech recognition service unavailable.")
#         return ""


# --- Helper: Extract audio stream URL ---
def get_audio_stream_url(query: str) -> str:
    opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "default_search": "ytsearch",
        "skip_download": True,
        "noplaylist": True,
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }

    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
            return info["entries"][0]["url"]
    except Exception:
        return ""


# --- Tool Functions (Executed LOCALLY) ---
def play_song(query: str) -> str:
    global current_song
    url = get_audio_stream_url(query)
    if not url:
        return f"Could not find or play '{query}'."

    media = vlc_instance.media_new(url, "--no-video")
    audio_player.set_media(media)
    audio_player.play()

    current_song = query
    return f"Playing '{query}' at normal speed (1.0x)."


def stop_audio() -> str:
    global current_song
    if audio_player.is_playing():
        audio_player.stop()
        old = current_song
        current_song = "None"
        return f"Stopped audio for '{old}'."
    return "Audio is already stopped."


def set_play_speed(speed: int) -> str:
    if speed not in SPEED_MAP:
        return "Invalid speed option (1–5)."

    rate = SPEED_MAP[speed]
    audio_player.set_rate(rate)
    return f"Playback speed set to {rate}x."


def toggle_pause_resume() -> str:
    if audio_player.is_playing():
        audio_player.pause()
        return f"Paused '{current_song}'."

    state = audio_player.get_state()
    if state == vlc.State.Paused:
        audio_player.pause()  # pause() toggles state
        return f"Resumed '{current_song}'."

    return "Nothing is playing to pause or resume."


# --- Function Registry ---
AVAILABLE_FUNCTIONS = {
    "play_song": play_song,
    "stop_audio": stop_audio,
    "set_play_speed": set_play_speed,
    "toggle_pause_resume": toggle_pause_resume,
}

# --- TOOL SCHEMA FOR GEMINI ---
AUDIO_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="play_song",
            description="Searches YouTube and plays audio.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
        types.FunctionDeclaration(
            name="stop_audio",
            description="Stops the currently playing audio.",
            parameters={"type": "object", "properties": {}},
        ),
        types.FunctionDeclaration(
            name="set_play_speed",
            description="Sets playback speed (1–5).",
            parameters={
                "type": "object",
                "properties": {"speed": {"type": "integer"}},
                "required": ["speed"],
            },
        ),
        types.FunctionDeclaration(
            name="toggle_pause_resume",
            description="Toggles between pause and resume.",
            parameters={"type": "object", "properties": {}},
        ),
    ]
)


# --- AGENT LOGIC ---
def run_agent_turn(client, model, user_prompt):
    print(f"\nYou: {user_prompt}")

    # Step 1 — Ask Gemini
    response = client.models.generate_content(
        model=model,
        contents=user_prompt,
        config=types.GenerateContentConfig(tools=[AUDIO_TOOL])
    )

    # If model answered normally
    if not response.function_calls:
        print("Gemini:", response.text)
        return

    # Step 2 — Execute tools locally
    tool_outputs = []

    for call in response.function_calls:
        fname = call.name
        fargs = dict(call.args)

        print(f"→ Gemini requested: {fname}({fargs})")

        if fname in AVAILABLE_FUNCTIONS:
            result = AVAILABLE_FUNCTIONS[fname](**fargs)
            print(f"← Tool result: {result}")

            tool_outputs.append(
                types.Part.from_function_response(
                    name=fname,
                    response={"result": result}
                )
            )

    # Step 3 — Send function results back to Gemini
    final = client.models.generate_content(
        model=model,
        contents=[
            types.Content(role="user", parts=[types.Part(text=user_prompt)]),
            response.candidates[0].content,
            types.Content(role="function", parts=tool_outputs),
        ],
        config=types.GenerateContentConfig(tools=[AUDIO_TOOL])
    )

    print("Gemini:", final.text)


working_mics = find_working_microphones()

if not working_mics:
    print("❌ No working microphones found.")
    exit()

mic_index = int(input("\nSelect microphone index from the working list: "))


# --- MAIN LOOP ---
if __name__ == "__main__":
    try:
        client = genai.Client()
        print("Gemini client initialized!\n")

        model = "gemini-2.5-flash"

        # Interactive Chat
        print("🎵 Gemini YouTube Audio Player")
        print("Type something like:")
        print(" • play believer by imagine dragons")
        print(" • pause")
        print(" • resume")
        print(" • stop")
        print(" • change speed to 4")
        print("-----------------------------------")

        while True:
            print("\nChoose input mode:")
            print("1. Type")
            print("2. Speak")
            # mode = input("Select (1/2): ").strip()

            mode = "2"

            if mode == "2":
                user_input = listen_for_voice()
                if not user_input:
                    continue
            else:
                user_input = input("\nYou: ").strip()

            if user_input.lower() in ["exit", "quit"]:
                break

            run_agent_turn(client, model, user_input)


    finally:
        audio_player.release()

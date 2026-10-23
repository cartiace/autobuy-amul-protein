"""
Loving Girlfriend Good Morning Message Generator.
Crafts affectionate, warm, caring, and encouraging morning messages
sent daily at 8:00 AM IST or on-demand via /goodmorningnow.
"""

import random
from typing import Optional

GIRLFRIEND_MESSAGES = [
    "Good morninggg handsome! ☀️🥰 I hope you slept so peacefully. Remember to drink a big glass of water, eat a good breakfast, and get that protein in today! You're going to do amazing today, I believe in you so much. Sending you the warmest hugs and sweetest kisses! 💕✨",
    
    "Rise and shine, my love! 🌸✨ Just wanted to be the first one to remind you how special and hardworking you are. Don't stress too much today, take little breaks, and make sure to smile! Loving you endlessly~ 💖😘",
    
    "Good morninggg baby! ☕🥐 I woke up thinking about you and just had to send you a little sunshine to start your day. Whatever you have planned today, you're going to crush it! Go get 'em, cutie! 💗🥰",
    
    "Morning sweetheart! ☀️💐 Did you sleep well? I hope you feel energized and ready to take on the world today. Don't forget that someone out here loves you and is cheering for you with all her heart! Muah! 💋✨",
    
    "Wakey wakey, cutie pie! 🥞🍓 Sending you a morning hug so warm and tight that you can feel it through the screen! Eat well, crush your workouts, and know that you make my heart so happy. Have the most wonderful day! 💕🥰",
    
    "Good morning, darling! 🌼 Hope your day is as bright, gentle, and lovely as your smile. Take good care of yourself today, stay hydrated, and don't skip your meals! Love you to the moon and back! 💖🌟",
    
    "Good morninggg to my favorite person in the entire universe! 🧸💖 Another day to be proud of you and all the effort you put in. Keep that handsome chin up, take deep breaths, and go shine bright today! ☀️✨",
    
    "Morning my love! ☀️ Just sipping my morning tea and missing you already. I hope your morning is calm and cozy, and that everything goes your way today. Sending you a million sweet kisses! 💕😘",
    
    "Rise and shine handsome! 🌅💪 Time to make today yours! Remember to hit your protein goals, stay positive, and don't let anything bring your mood down. I'm always in your corner, baby! 💗✨",
    
    "Good morning, sunshine! ☀️💛 Just a little reminder that you are deeply appreciated, admired, and loved. Thank you for being you. Have a productive and joyful day today, sweetheart! 💕🥰",
    
    "Hey you! Good morninggg! 🌻✨ Hope you woke up with a big smile on your face! Go smash your to-do list today, and don't forget I'm waiting to hear all about your day later! Hugs and kisses! 💖😊",
    
    "Morning baby! ☕✨ I wish I could make you breakfast in bed today, but for now here's all my love wrapped up in this message! Be kind to yourself today, okay? You're doing great! Love you tons! 💗😘"
]

def get_loving_morning_message(name: Optional[str] = None) -> str:
    """Returns a sweet, randomized loving girlfriend morning message."""
    msg = random.choice(GIRLFRIEND_MESSAGES)
    if name and name.strip() and not name.startswith("User "):
        # Personalize if a real name or nickname is provided
        clean_name = name.strip().lstrip("@")
        if random.random() < 0.4:
            msg = f"Hey {clean_name}! " + msg
    return msg

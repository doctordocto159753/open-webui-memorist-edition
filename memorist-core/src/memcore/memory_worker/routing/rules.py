from __future__ import annotations

import re

AI_RECEIVER = re.compile(r"\b(ai|assistant|model|chatgpt|you)\b|هوش مصنوعی|دستیار|مدل|تو|شما", re.I)
TEAM_RECEIVER = re.compile(
    r"product team|team|developer|squad|تیم|توسعه‌دهنده|برنامه‌نویس|اسکواد",
    re.I,
)
HIGH_PRIORITY_INSTRUCTION = re.compile(
    r"\b(must|should|have to|do not|don't|add|define|create|implement|route|use)\b|"
    r"باید|حتما|وظیفه|تعریف کن|اضافه کن|پیاده‌سازی کن|استفاده کن|نکن",
    re.I,
)
JIRA_CONTEXT = re.compile(r"\bjira\b|جیرا", re.I)
PROCESS_CONTEXT = re.compile(
    r"\b(process|workflow|project|product|pipeline|system logic|configuration)\b|"
    r"فرایند|فرآیند|ورک‌فلو|جریان کار|پروژه|محصول|منطق سیستم|کانفیگ",
    re.I,
)
METALINGUAL_CONTEXT = re.compile(
    r"\b(term|terminology|wording|definition|translation|meaning|prompt wording)\b|"
    r"اصطلاح|واژه|تعریف|ترجمه|معنی|منظور|عبارت|متن پرامپت",
    re.I,
)
EMOTIVE_CONTEXT = re.compile(
    r"\b(prefer|want|like|dislike|frustrated|annoyed|approve|wish)\b|"
    r"ترجیح|می‌خواهم|دوست دارم|ناراحتم|کلافه|راضی|نگران",
    re.I,
)
POETIC_CONTEXT = re.compile(r"\b(slogan|style|rhythm|tone|branding)\b|شعار|سبک|لحن|برند", re.I)
RESOURCE_CONTEXT = re.compile(r"https?://|www\.|file:|مسیر فایل|لینک|منبع", re.I)
PRIVACY_CONTEXT = re.compile(
    r"\b(secret|password|token|api key|privacy)\b|رمز|توکن|کلید|حریم خصوصی",
    re.I,
)

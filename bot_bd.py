import os
import sys
import requests
import telegram
import config
import psycopg2
import ast
import html
import json
import logging
import traceback
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, ConversationHandler
from functools import wraps
from random import choice, shuffle
from threading import Thread
# from datetime import time
from bs4 import BeautifulSoup
from google_trans_new import google_translator
PORT = int(os.environ.get('PORT', '5000'))
PLAY = range(1)  # var for ConversationHandler

logger = logging.getLogger(__name__)


def send_typing_action(func):
    """Sends typing action while processing func command."""

    @wraps(func)
    def command_func(update, context, *args, **kwargs):
        context.bot.send_chat_action(chat_id=update.effective_message.chat_id, action=telegram.ChatAction.TYPING)
        return func(update, context, *args, **kwargs)

    return command_func


def error_handler(update, context):
    """Log the error and send a telegram message to notify the developer."""
    # Log the error before we do anything else, so we can see it even if something breaks.
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    # traceback.format_exception returns the usual python message about an exception, but as a
    # list of strings rather than a single string, so we have to join them together.
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = ''.join(tb_list)

    # Build the message with some markup and additional information about what happened.
    # You might need to add some logic to deal with messages longer than the 4096 character limit.
    update_str = update.to_dict() if isinstance(update, telegram.Update) else str(update)
    message = (
        f'An exception was raised while handling an update\n'
        f'<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}'
        '</pre>\n\n'
        f'<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n'
        f'<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n'
        f'<pre>{html.escape(tb_string)}</pre>'
    )

    # Finally, send the message
    context.bot.send_message(chat_id='-1001576881749', text=message, parse_mode=telegram.ParseMode.HTML)


# Restart bot block
def stop_and_restart(updater):
    """Gracefully stop the Updater and replace the current process with a new one"""
    updater.stop()
    os.execl(sys.executable, sys.executable, *sys.argv)


def restart(update, context):
    update.message.reply_text('Bot is restarting...')
    Thread(target=stop_and_restart).start()


def bad_command(update, context) -> None:
    """Raise an error to trigger the error handler."""
    context.bot.wrong_method_name()


# Helper function that simplifies sql queries
def send_query(sql_query: str, var=None):
    record = None
    conn = psycopg2.connect(config.DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute(sql_query, var)
    if sql_query.strip().lower()[:6] == 'select':
        record = cursor.fetchall()
    cursor.close()
    conn.commit()
    conn.close()
    return record


def log_user(uid, update):
    send_query(f"""insert into users select '{uid}',
                    '{update.message.chat.first_name}',
                    '{update.message.chat.last_name}', 
                    '{update.message.chat.username}'
                    where not exists (select uid from users where uid = '{uid}'); """)


def conjugate(input_):
    """ Try to get verb conjugation via reverso.net """
    word_seq = input_.split()  # needed for phrasal verbs
    try:
        response = requests.get(
            f'https://conjugator.reverso.net/conjugation-english-verb-{word_seq[0]}.html',  # conjugate only first word
            headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(response.content, 'html.parser')
        for w in soup.find_all("a", "targetted-word-transl", tooltip="Existing infinitive"):
            word_seq[0] = w.text.strip()
    except Exception:
        pass
    return ' '.join(word_seq)


# Translation functions: get BeautifulSoup with parse func, extract translation and examples from it with others
def translation(word: str) -> str:
    """
    Translate word in en-ru language pair. First step - function tries to translate word with reverso.net and appends
    all translations to a list. Second step - translate word with google translate and append it to list. Using these
    steps because google_translate is less accurate but translates wider range of words. Third step - russian word
    normalization via request to opencorpora.org
    :param word: word in English or Russian
    :return: most popular [normalized] word translation
    """
    _list = []  # list with word translations
    # Try to get translation via reverso.net
    response = requests.get(
        f'https://context.reverso.net/translation/english-russian/{word}',
        headers={'User-Agent': 'Mozilla/5.0'})
    soup = BeautifulSoup(response.content, 'html.parser')
    try:
        for w in soup.find_all("a", {"class": "translation"}):
            trans = w.text.strip()
            _list.append(trans)
        _list.pop(0)  # popping word 'Translation'
    except Exception:
        pass
    try:
        for w in soup.find_all("div", {"class": "translation"}):
            trans = w.text.strip()
            _list.append(trans)
    except Exception:
        pass

    # Append translation via google_translator in case of exception on previous step
    translator = google_translator()
    language = translator.detect(word)
    if language[0] == 'en':
        _list.append(translator.translate(word, lang_src=language[0], lang_tgt='ru'))
    elif language[0] == 'ru':
        _list.append(translator.translate(word, lang_src=language[0], lang_tgt='en'))

    # Try to get normalized word form and ignore the move if Exception occurs
    result = _list[0]
    try:
        _list = []
        response = requests.get(
            f'http://opencorpora.org/dict.php?search_form={result}&act=lemmata',
            headers={'User-Agent': 'Mozilla/5.0'})
        for w in BeautifulSoup(response.content, 'html.parser').find_all("a", href=True):
            word = w.text.strip()
            _list.append(word)
        result = _list[14].split()[1]
    except Exception:
        pass

    return result


def examples(word: str) -> list:
    """
    Get examples from Twinword API
    :param word: word in English
    :return: list with examples
    """
    url = "https://twinword-word-graph-dictionary.p.rapidapi.com/example/"
    querystring = {'entry': word}

    response = requests.request("GET", url, headers=config.HEADERS, params=querystring)
    # Create dictionary from the response result
    response_dict = ast.literal_eval(response.text)
    examples_list = response_dict['example']
    return examples_list


# Start message
def start(update, context):
    uid = str(update.message.chat_id)

    # Send message
    context.bot.send_message(chat_id=update.effective_chat.id,
                             text="Привет, я Voc! Я могу помочь тебе развить свой словарь.\n"
                                  "Команда /help покажет, что я могу")
    # Log action into user_actions table
    send_query(f"insert into user_actions (uid, action) values ('{uid}', 'start')")
    log_user(uid, update)  # Log user into users table


# Help handler with readme
def help_me(update, context):
    uid = str(update.message.chat_id)

    # Send message
    # context.bot.send_message(chat_id=update.effective_chat.id,
    #                          text="Add words to your dictionary by just sending them to me.\n"
    #                               "I will translate them, find usage and add them to your voc-a-bulary.\n\n"
    #                               "Sometimes i can't find words you want to add. "
    #                               "In this case you can add word using /add [word][-][meaning].\n"
    #                               "You can delete words with /delete [word] command.\n"
    #                               "Or you can edit them with /edit [word][-][new translation] command.\n"
    #                               "To see your vocabulary type /voc.\n"
    #                               "Sending /stats will display your learning summary statistics\n\n"
    #                               "To start learning words type /play. I will send you word, it's usage and "
    #                               "You have to guess the right translation from a given list\n\n"
    #                             "And you're always welcome to message my owner any concerns or wishes with /m command"
    #                          )
    context.bot.send_message(chat_id=update.effective_chat.id,
                             text="Чтобы добавить слово в словарь, отправь его мне в чат.\n"
                                  "Я сделаю перевод и добавлю его в твой личный словарь.\n"
                                  "Если это слово будет на русском - я переведу его\n\n"
                                  "Иногда у меня не получается найти нужное слово. "
                                  "В этом случае ты можешь использовать команду /add [слово][-][значение].\n"
                                  "Удалить слово ты можешь командой /delete [слово] .\n"
                                  "А изменить значение можно командой /edit [слово][-][новое значение].\n"
                                  "Твой личный словарь откроется по команде /voc.\n"
                                  "Если отправишь /stats , я покажу небольшую статистику освоения словаря\n\n"
                                  "Активное изучение начинается с команды /play. Я пришлю тебе слово и "
                                  "пример его употребления. А тебе нужно будет выбрать правильный вариант из списка\n\n"
                                  "Оставить обратную связь для мешка, который меня сделал, можно командой /m \U0001F60B"
                             )
    # Log action into user_actions table
    send_query(f"insert into user_actions (uid, action) values ('{uid}', 'help')")
    log_user(uid, update)  # Log user into users table


# Bot commands block
@send_typing_action
def add_word(update, context):
    """
    Add word to dictionaries. If word is already in common dict, add only in users one.
    If word is in both dicts, change message and print translation
    """
    var_text = 'New word'  # Variational phrase depending if word is already added
    uid = str(update.message.chat_id)
    word = update.message.text.lower()
    word = conjugate(word)  # conjugation of verbs

    record = send_query(f"select count(word) from words where word = '{word}'")
    if record[0][0] == 0:
        try:
            translation_result = translation(word)
            translation_result = translation_result.lower()
            examples_list = examples(word)
            if len(examples_list) != 0:
                examples_list = (examples_list,)
                sql_query = f"insert into words (word, meaning, examples) values (%s, %s, %s)"
                send_query(sql_query, (word, translation_result, examples_list))
            else:
                # Log action into user_actions table
                send_query(f"insert into user_actions (uid, action) values ('{uid}', 'add_word_fail_example')")
                context.bot.send_message(chat_id=update.effective_chat.id,
                                         text=f"Не получилось найти пример использования этого слова \U0001F914")
        except Exception:
            # Log action into user_actions table
            send_query(f"insert into user_actions (uid, action) values ('{uid}', 'add_word_fail')")
            context.bot.send_message(chat_id=update.effective_chat.id, text=f"Я не смог найти это слово \U0001F914")
            raise

    # Add word to user personal dict if it's not there. Else change output message
    record = send_query(
        f"select count(word), bool_or(is_deleted) from user_words where uid = '{uid}' and word = '{word}'; ")
    if record[0][0] == 0:
        send_query(f"insert into user_words (uid, word) values ('{uid}', '{word}')")
    elif record[0][1] == 1:
        send_query(f"update user_words set is_deleted = False where uid = '{uid}' and word = '{word}'")
    elif record[0][1] == 0:
        var_text = 'Слово уже есть словаре'

    # Send message
    record = send_query(f"""
                        select is_edited, meaning, examples, edit
                        from user_words uw left join words w on w.word = uw.word
                        where uid = '{uid}' and w.word = '{word}';""")

    real_meaning = record[0][1] if record[0][0] == 0 else record[0][3]
    if record[0][2]:
        string_ = f"Пример:\n{choice(record[0][2])}"

    else:
        string_ = ''
    context.bot.send_message(chat_id=update.effective_chat.id,
                             text=f"{var_text}:\n{word} - {real_meaning}\n\n" + string_)
    # Log action into user_actions table
    send_query(f"insert into user_actions (uid, action) values ('{uid}', 'add_word')")

    log_user(uid, update)  # Log user into users table


@send_typing_action
def add_words_manually(update, context):
    """
    /add to manually add words to user-words table
    """
    uid = str(update.message.chat_id)
    message = ' '.join(context.args)
    word, meaning = message.split('-')
    word, meaning = word.strip(), meaning.strip()
    record = send_query(f"select count(*) from user_words where word = '{word}'; ")
    if record[0][0] == 1:
        send_query(f"""update user_words set is_deleted = False, is_edited = True, edit = '{meaning}' 
                        where uid = '{uid}' and word = '{word}'; """)
    elif record[0][0] == 0:
        send_query(f"""insert into user_words (uid, word, is_edited, edit) 
                        values ('{uid}', '{word}', True, '{meaning}'); """)
    context.bot.send_message(chat_id=update.effective_chat.id,
                             text=f"Слово {word} добавлено")
    # Log action into user_actions table
    send_query(f"insert into user_actions (uid, action) values ('{uid}', 'add_manual')")


@send_typing_action
def translate_russian(update, context):
    """
    If word is in Russian (this filter is in handler) then translate it to English
    """
    uid = str(update.message.chat_id)
    word = update.message.text.lower()
    try:
        translation_result = translation(word)
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text=f'Перевод:\n{word} - {translation_result}')
        # Log action into user_actions table
        send_query(f"insert into user_actions (uid, action) values ('{uid}', 'translate_russian')")
    except Exception:
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text=f"Упс, проблемы \U0001F630")
        # Log action into user_actions table
        send_query(f"insert into user_actions (uid, action) values ('{uid}', 'translate_russian_fail')")


# Delete word (word pair) sent after /delete
@send_typing_action
def delete_word(update, context):
    uid = str(update.message.chat_id)
    word = ' '.join(context.args)

    # Look if word is in user-words dict and is not deleted
    record = send_query(f"select is_deleted from user_words where uid = '{uid}' and word = '{word}';")
    if record and record[0][0] == 0:
        send_query(f"update user_words set is_deleted = True where uid = '{uid}' and  word = '{word}'")
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text=f"Слово *{word}* удалено", parse_mode=telegram.ParseMode.MARKDOWN)
        # Log action into user_actions table
        send_query(f"insert into user_actions (uid, action) values ('{uid}', 'delete_word')")
    else:
        context.bot.send_message(chat_id=update.effective_chat.id, text=f'Упс. Что-то пошло не так')
        # Log action into user_actions table
        send_query(f"insert into user_actions (uid, action) values ('{uid}', 'delete_word_fail')")


# Edit word
@send_typing_action
def edit(update, context):
    uid = str(update.message.chat_id)
    message = ' '.join(context.args)
    word, new_meaning = message.split('-')
    word, new_meaning = word.strip(), new_meaning.strip()

    sql_query = f"""
                select w.word, meaning, examples, edit, is_edited, is_deleted 
                from user_words uw
                join words w
                    on w.word = uw.word
                    and uw.uid = '{uid}';
                """
    record = send_query(sql_query)
    if record[0][0]:
        send_query(f"""update user_words set is_edited = True, edit = '{new_meaning}'
                       where uid = '{uid}' and word = '{word}'""")

        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text=f'Новое значение:\n\n'f'{word} - {new_meaning} ')
        # Log action into user_actions table
        send_query(f"insert into user_actions (uid, action) values ('{uid}', 'edit')")
    else:
        context.bot.send_message(chat_id=update.effective_chat.id, text=f"Пока я не храню такое слово")
        # Log action into user_actions table
        send_query(f"insert into user_actions (uid, action) values ('{uid}', 'edit_fail')")


# Prints whole vocabulary as message
@send_typing_action
def voc(update, context):
    uid = str(update.message.chat_id)
    sql_query = f"""
                select uw.word, meaning, examples, edit, is_edited 
                from user_words uw
                left join words w
                    on w.word = uw.word
                where uw.uid = '{uid}' and is_deleted = False
                order by 1;
                """
    record = send_query(sql_query)
    response = f"Сейчас в словаре {len(record)} слов:\n\n"
    for i in range(len(record)):
        if record[i][4] == 0:
            response += record[i][0] + ' - ' + record[i][1] + '\n'
        elif record[i][4] == 1:
            response += record[i][0] + ' - ' + record[i][3] + '\n'
    if not record:
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text='Словарик пока пуст. Пришли мне несколько слов \U0001F61C')
    else:
        context.bot.send_message(chat_id=update.effective_chat.id, text=response)
    # Log action into user_actions table
    send_query(f"insert into user_actions (uid, action) values ('{uid}', 'voc')")


# Play functions that prints example - word and 3 possible translations
@send_typing_action
def play_game(update, context):
    """Main game function that takes random non-deleted word and create bla-lba-lba"""
    uid = str(update.message.chat_id)
    record = send_query(f"""select uw.word from user_words uw join words w on w.word = uw.word
                            where uid = '{uid}' and is_deleted = False and translation_score <= 5;""")

    if len(record) < 3:
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text='Чтобы сохранилась интрига, для игры нужно 3 слова в словарике')
    elif len(record) >= 3:  # Больше 3 слов и прошлое слово отличается от нового
        user_words = [record[i][0] for i in range(len(record))]
        main_word = choice(user_words)
        record = send_query(f"select max(word) from games where uid = '{uid}';")
        while record[0][0] == main_word:    # pick word until != last round word
            main_word = choice(user_words)
        game_words = [main_word]  # list with quiz words
        list_len = len(user_words) // 6  # len of list with dummies
        list_len = 2 if list_len <= 2 else 8 if list_len >= 8 else list_len
        user_words.remove(main_word)
        for _ in range(list_len):
            dummy = choice(user_words)
            game_words.append(dummy)
            user_words.remove(dummy)

        shuffle(game_words)
        example = ''
        meaning = []
        for i in range(len(game_words)):
            record = send_query(f"""select uw.word, edit, is_edited, meaning, examples, translation_score
                                                from user_words uw join words w on w.word = uw.word
                                                where uid = '{uid}' and is_deleted=False and uw.word='{game_words[i]}';
            """)
            real_meaning = record[0][3] if record[0][2] == 0 else record[0][1]
            meaning.append(real_meaning)
            if record[0][0] == main_word:
                example = choice(record[0][4])
                send_query(f"""update games 
                                    set word = '{main_word}',
                                    answer_var = {i + 1}, 
                                    translation_score = {record[0][5]},
                                    meaning = '{real_meaning}'
                                where uid = '{uid}'; """)
        reply_string = '\n'.join([f"{i+1}. {meaning[i]}" for i in range(len(meaning))])
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text=f"{example}\n\nЗначение слова *{main_word}*:\n" + reply_string +
                                      "\n\nВведи номер правильного ответа или 0 для *выхода*",
                                 parse_mode=telegram.ParseMode.MARKDOWN)


@send_typing_action
def play_intro(update, context):
    uid = str(update.message.chat_id)
    send_query(f"""insert into games select '{uid}', '', 0, 0, ''
                    where not exists (select uid from games where uid = '{uid}'); """)
    context.bot.send_message(chat_id=update.effective_chat.id,
                             text="Сейчас я отправлю слово. Попробуй выбрать правильный вариант ответа\n"
                                  "После 5 правильных ответов я помечу, что слово изучено и уберу его из словарика",
                             parse_mode=telegram.ParseMode.MARKDOWN)
    play_game(update, context)
    send_query(f"insert into user_actions (uid, action) values ('{uid}', 'play')")
    return PLAY


@send_typing_action
def play(update, context):
    uid = str(update.message.chat_id)
    word = update.message.text
    record = send_query(f"""select word, answer_var, translation_score, meaning
                            from games where uid = '{uid}'; """)
    if word == str(record[0][1]):
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text=f"Отлично! \U0001F64C\n\nСчет *{record[0][0]}*: {record[0][2] + 1}",
                                 parse_mode=telegram.ParseMode.MARKDOWN)
        send_query(f"insert into user_actions (uid, action) values ('{uid}', 'win')")
        # If it's 5th guess for the word mark word as deleted else add 1 score point
        if record[0][2] == 4:
            send_query(f"""update user_words set is_deleted=True, translation_score = {record[0][2] + 1}
                            where uid = '{uid}' and word = '{record[0][0]}';""")
            context.bot.send_message(chat_id=update.effective_chat.id,
                                     text=f"Так держать! Слово *{record[0][0]}* изучено!",
                                     parse_mode=telegram.ParseMode.MARKDOWN)
            send_query(f"insert into user_actions (uid, action) values ('{uid}', 'translation_mastered')")
        else:
            send_query(f"""update user_words set translation_score = {record[0][2] + 1}
                            where uid = '{uid}' and word = '{record[0][0]}' ;""")

    elif word != str(record[0][1]):
        score = 0 if record[0][2] == 0 else record[0][2] - 1
        context.bot.send_message(chat_id=update.effective_chat.id,
                                 text=f"Это ошибка \U0001F609\nЗначение слова {record[0][0]} - {record[0][3]} "
                                      f"\n\nСчет слова *{record[0][0]}*: {score}",
                                 parse_mode=telegram.ParseMode.MARKDOWN)
        send_query(f"insert into user_actions (uid, action) values ('{uid}', 'lose')")
        if record[0][2] > 0:
            send_query(f"""update user_words set translation_score = {record[0][2] - 1}
                                        where uid = '{uid}' and word = '{record[0][0]}' ;""")

    play_game(update, context)
    return PLAY


def cancel(update, context):
    context.bot.send_message(chat_id=update.effective_chat.id, text=f"Приходи играть ещё! \U0001F64B")
    return ConversationHandler.END


# Print statistics function
@send_typing_action
def user_statistics(update, context):
    uid = str(update.message.chat_id)
    record = send_query(f"""select  count(case when is_deleted = False then word end), 
                                    count(case when translation_score = 5 then word end)
                            from user_words where uid = '{uid}'; """)
    reply = f"Из *{record[0][0] + record[0][1]}* добавленных слов успешно изучено *{record[0][1]}*.\n\n"
    record = send_query(f"""select count(case when action in ('win', 'lose') then 1 end),
                                    count(case when action = 'win' then 1 end),
                                    count(case when action = 'translation_mastered' then 1 end)
                            from user_actions where uid = '{uid}' and dttm >= now() - interval '7 days'
                                    ;""")
    reply += f"Удачных попыток за последнюю неделю: {record[0][1]}.\nВсего попыток: {record[0][0]}. " \
             f"\nИзучено слов: {record[0][2]}.\n\nПрекрасная работа, не останавливайся \U0001F44F"

    context.bot.send_message(chat_id=update.effective_chat.id,
                             text=reply,
                             parse_mode=telegram.ParseMode.MARKDOWN)
    send_query(f"insert into user_actions (uid, action) values ('{uid}', 'stats')")


# Reminder function
# def reminder(context: telegram.ext.CallbackContext):
#     record = send_query(f"select uid from user_actions group by uid having max(dttm) <= now() - interval '3 days'; ")
#     for i in range(len(record)):
#         context.bot.send_message(chat_id=record[i][0],
#                                  text="We haven't met for a while. I can't study without you",
#                                  parse_mode=telegram.ParseMode.MARKDOWN)
#         send_query(f"insert into user_actions (uid, action) values ('{record[i][0]}', 'reminder')")


def message_owner(update, context):
    uid = str(update.message.chat_id)
    message = ' '.join(context.args)
    context.bot.send_message(chat_id=config.MY_ID, text=f"{uid}: {message}")


def main():
    # Init
    updater = Updater(token=config.TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    # Add slash commands handlers
    dispatcher.add_handler(CommandHandler('delete', delete_word))
    dispatcher.add_handler(CommandHandler('edit', edit))
    dispatcher.add_handler(CommandHandler('voc', voc))
    dispatcher.add_handler(CommandHandler('start', start))
    dispatcher.add_handler(CommandHandler('help', help_me))
    dispatcher.add_handler(CommandHandler('stats', user_statistics))
    dispatcher.add_handler(CommandHandler('m', message_owner))
    dispatcher.add_handler(CommandHandler('add', add_words_manually))

    # Add add_word functionality to bot
    add_word_handler = MessageHandler(Filters.text & (~Filters.command) & Filters.regex(r'[^\u0400-\u04FF]') &
                                      Filters.regex(r'[^0-9]'), add_word)
    dispatcher.add_handler(add_word_handler)

    # Add translate from russian functionality to bot [unicode filter is here]
    translate_russian_handler = MessageHandler(Filters.text & (~Filters.command) &
                                               Filters.regex(r'[\u0400-\u04FF]') & Filters.regex(r'[^0-9]'),
                                               translate_russian)
    dispatcher.add_handler(translate_russian_handler)

    # Play mode with ConversationHandler uses 3 functions: play_intro, play_init and play_game inside them
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('play', play_intro)],
        states={PLAY: [MessageHandler(Filters.text & (~Filters.command) & Filters.regex(r'[1-9]'), play)]},
        fallbacks=[MessageHandler(Filters.text & (~Filters.command) & Filters.regex(r'[0]'), cancel)],
        allow_reentry=True
    )
    dispatcher.add_handler(conv_handler)

    # Reminder job queue
    # j = updater.job_queue
    # j.run_daily(reminder, time=time(15, 10, 0))

    dispatcher.add_handler(CommandHandler('r', restart, filters=Filters.user(username='@ima_qt')))

    # test command for error handler and himself
    dispatcher.add_handler(CommandHandler('bad_command', bad_command, filters=Filters.user(username='@ima_qt')))
    dispatcher.add_error_handler(error_handler)

    # Start the Bot
    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.start_webhook(listen="0.0.0.0", port=int(PORT), url_path=config.TOKEN,
                          webhook_url='https://voc-a-bot.herokuapp.com/' + config.TOKEN)

    # updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()

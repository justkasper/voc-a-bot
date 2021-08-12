import psycopg2
import config

conn = psycopg2.connect(config.DATABASE_URL)
# Create table logging user actions
create_user_actions = f"""
            create table if not exists user_actions 
            (
            uid varchar(20),
            action varchar(30),
            dttm timestamp default (now())
            );"""
# Create table with user personal dictionary
create_user_words = f"""
            create table if not exists user_words
            (
            uid varchar(20),
            word varchar(30),
            edit varchar(30),
            is_edited boolean default false,
            is_deleted boolean default false,
            translation_score int4 default (0)
            );"""
# Create table with common vocabulary for all users
create_words = f"""
            create table if not exists words
            (
            word varchar(30),
            meaning varchar(30),
            examples text[]
            );"""
create_games = f"""
            create table if not exists games
            (
            uid int,
            word varchar(30),
            answer_var int,
            translation_score int
            );"""
create_users = f"""
            create table if not exists users
            (
            uid int,
            first_name varchar(30),
            last_name varchar(30),
            username varchar(30)
            );"""

cursor = conn.cursor()
cursor.execute(f"drop table user_actions;")
cursor.execute(f"drop table user_words;")
cursor.execute(f"drop table words;")
cursor.execute(f"drop table games;")
cursor.execute(create_user_actions)
cursor.execute(create_user_words)
cursor.execute(create_words)
cursor.execute(create_games)
cursor.execute(f"drop table users;")
cursor.execute(create_users)

conn.commit()  # <--- makes sure the change is shown in the database
cursor.close()
conn.close()


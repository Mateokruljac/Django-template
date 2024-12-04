# Run on Windows (PyCharm)

```
python manage.py runserver --settings=django-template.settings.local
```

# Run on Mac (Terminal) or on Server using docker

```
docker-compose up
```

# Run on Server without docker

```
echo "\nDJANGO_SETTINGS_MODULE=django-template.settings.local" >> .env
python manage.py runserver

```

# Packages

```
https://python-docx.readthedocs.io/en/latest/#what-it-can-do
https://github.com/elapouya/python-docx-template
https://docxtpl.readthedocs.io/en/latest/

```

# Docker commands

```

# start
docker-compose up -d --build

# list active containers
docker ps

# turn off
docker-compose down

# web logovi
docker_logs web

# db logovi
docker_logs db

# create migrations
docker exec $(docker ps -qf "name=web") python manage.py makemigrations

# run migrations
docker exec $(docker ps -qf "name=web") python manage.py migrate

# run tests
docker exec $(docker ps -qf "name=web") python manage.py test

```

```

# Live server

```

# rename project

"django-template" globaly change to "project-name"


```




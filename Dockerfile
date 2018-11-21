# foo

FROM tiangolo/uwsgi-nginx-flask:python2.7
MAINTAINER Alex Headley <aheadley@waysaboutstuff.com>

ENV UWSGI_INI /app/docker/uwsgi.ini
ENV OMAKASE_DEBUG STEAM_API_KEY MEMCACHED_SERVERS 

COPY ./ /app/

RUN pip install -r /app/requirements.txt

RUN ln -s /app/docker/nginx-extra.conf /etc/nginx/conf.d/extra.conf

import logger
import json
import time
from flask import Flask
from flask_sock import Sock
import os
import redis

log = logger.log(__name__)

app = Flask(__name__)
sock = Sock(app)

# https://simple-websocket.readthedocs.io/en/latest/api.html#the-server-class
# in seconds
app.config['SOCK_SERVER_OPTIONS'] = {'ping_interval': 25}

@sock.route('/ws')
def websocket(ws):
    redis_client = redis.Redis(host="redis")
    # queue_counters = {}
    # queue_max = {}
    while True:
        # did the client send us anything?
        data = ws.receive(0)

        if data:
            # we got something!
            continue

        # if not, we check redis for any messages to send to the client. find
        # the queue names in redis, then will all start with "websocket_" and
        # then have the chapter key and image index, so we can do a pattern
        #   match to find the right one for this client.
        
        # Example: 
        #   redis_key =  f"websocket_{chapter_key}_{image_index}"

        # loop all keys that match the pattern "websocket_*"
        for key in redis_client.scan_iter("websocket_*"):
            log.info(f"Checking redis queue for {key}")
            # if we find one, we check if it has any messages to send.
            try:
                message = redis_client.lpop(key)
            except redis.exceptions.ResponseError as e:
                log.error(f"Error popping from redis key {key}: {e}")
                # you don't belong here.  Die!
                redis_client.delete(key)
                continue

            queue_name = key.decode().removeprefix("websocket_")
            
            if message:
                log.info(f"Got message from redis: {message}")
                # the first message _must_ be the number of steps.
                # if you guess wrong it won't break anything important, but it won't look nice.
                
                # do we want to just dump it on the client?  NO, the client
                # side is htmx.
                # "Content that is sent down from the websocket will be parsed as HTML and swapped in by the id property, using the same logic as Out of Band Swaps."
                # current use case is progress bars.  It has already been placed.
                # the stuff after websocket_ is what we need to use to determine the correct id to target with the oob swap.
                _, chapter_key, image_index = key.decode().split("_", 2)
                                
                # value initialized itself, first value is 1
                value = redis_client.incr(queue_name + "_counter")
                log.info(f"value: {value}, message: {message}")

                if value == 1:
                    # the first message is the number of total steps for our % calculation
                    redis_client.set(queue_name + "_steps", int(float(message)))

                # why put the max value in redis instead of local memory?
                # Because if the service restarts mid-generation, we lose all
                # local memory, but redis will persist.  This service becomes stateless.
                max_value = redis_client.get(queue_name + "_steps")
                if max_value is None:
                    # this can happen if the service restarts mid-stream.  No biggie.
                    log.error('No max value found for queue %s, defaulting to 100', queue_name)
                    max_value = 100
                    redis_client.set(queue_name + "_steps", max_value)
                
                max_value = int(max_value)

                log.info(f"Progress for {chapter_key} image {image_index}: {value}/{max_value}")
                pvalue = int((value / int(max_value)) * 100)

                # there are multiple progress bars for the same image.
                swap_oob = f"outerHTML:.image_{image_index}"
                response = f'<wa-progress-ring hx-swap-oob="{swap_oob}" class="image_{image_index}" value="{pvalue}"></wa-progress-ring>'

                log.info(f"Sending message to client: {response}")
                ws.send(response)

                if message in ["Complete", b"Complete"]:
                    log.info(f'Image generation complete.  Done with queue [{chapter_key}, {image_index}]')

                    if pvalue != 100:
                        log.warning(f"Image generation complete but progress is {pvalue}%, expected 100%")
                        log.warning(f"Max value provided was {max_value}, Final value provided is {value}")

                    # if it's a completion message, we can delete the queue.
                    log.info('Clearing redis entries for %s / %s', key, queue_name)
                    redis_client.delete(key)
                    redis_client.delete(queue_name + "_steps")
                    redis_client.delete(queue_name + "_counter")
                    
                    author, title, chapter_number, language = json.loads(chapter_key)
                    height = 512
                    img_url = os.path.join(
                        "library",
                        author,
                        title,
                        chapter_number,
                        language,
                        "images",
                        str(height),
                        f"{image_index}.png"
                    )
                    swap_oob = f"outerHTML:.image_{image_index}"
                    message = f'<img hx-swap-oob="{swap_oob}" class="image_{image_index}" style="height: 50%; width: 50%; align-self: center;" src="/{img_url}"/>'
                    ws.send(message)


        time.sleep(0.25)
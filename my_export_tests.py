from my_export import export_data

d = [
    {'name': 'Allegro', 'url': 'https://allegro.tech/2018/08/postmortem-why-allegro-went-down.html', 'description': 'E-commerce site went down after a sudden traffic spike caused by a marketing campaign. The outage was caused by a configuration error in cluster resource management which prevented more service instances from starting even though hardware resources were available.', 'error': False},
    {'name': 'Cloudflare', 'url': 'https://web.archive.org/web/20211006135542/https://blog.cloudflare.com/todays-outage-post-mortem-82515/', 'description': 'A bad config (router rule) caused all of their edge routers to crash, taking down all of Cloudflare.', 'error': False},
]

export_data(d, "test_output", "pm.jsonl", "pm.html")
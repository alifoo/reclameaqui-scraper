# reclameaqui-scraper

This webscraper was built with Playwright and BeautifulSoup libraries. It is designed to scrape data from the Reclame Aqui website to collect data (without saving any personal information) to create large datasets for training a machine learning model.

Currently, it is running on AWS EC2 to be able to run 24/7.

## Commands

SSH into ec2 instance
```
ssh -i scraper-key ubuntu@3.235.182.148
```

Copy script into machine
```
scp -i scraper-key main.py ubuntu@3.235.182.148:/home/ubuntu/scraper/
```

Run script in the background
```
nohup python -u main.py > output.log 2>&1 &
```

Visualize logs while running
```
cd /home/ubuntu/scraper/
source .venv/bin/activate
tail -n 100 -f output.log
```

## Durable validator payout code

Reviewed payout implementations live in [`payouts/`](payouts/README.md), with
separate JSM/Cypher policies, offline tests and controlled deployment/recovery
runbooks. They do not automatically replace the historical scripts below.
Normal payout execution and cron activation are owner-performed financial steps.

Run offline validation with `python3 -B tests/run_tests.py`.

---

git clone  

edit run.sh to have correct db and table  

sudo chmod +x run.sh  


./run.sh


## Grafana setup

sudo apt update && sudo apt upgrade -y
wget -q -O - https://packages.grafana.com/gpg.key | gpg --dearmor | sudo tee /usr/share/keyrings/grafana.gpg > /dev/null
echo "deb [signed-by=/usr/share/keyrings/grafana.gpg] https://packages.grafana.com/oss/deb stable main" | sudo tee -a /etc/apt/sources.list.d/grafana.list
sudo apt update
sudo apt install grafana -y
sudo systemctl start grafana-server
sudo systemctl status grafana-server
sudo grafana-cli plugins install grafana-timestream-datasource
sudo systemctl restart grafana-server
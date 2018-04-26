cyclebot:
	python cyclebot.py

deploy:
	zappa deploy prod

lint:
	flake8 .

package:
	zappa package prod

prune:
	python prune.py

requirements:
	pip install -r requirements.txt

rollback:
	zappa rollback prod -n 1

schedule:
	zappa schedule prod

ship: update prune

status:
	zappa status prod

tail:
	zappa tail prod --since 5m

test:
	pytest

undeploy:
	zappa undeploy prod --remove-logs

unschedule:
	zappa unschedule prod

update:
	zappa update prod

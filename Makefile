cyclebot:
	python cyclebot.py

deploy:
	zappa deploy prod

lint:
	flake8 cyclebot.py

package:
	zappa package prod

requirements:
	pip install -r requirements.txt

rollback:
	zappa rollback prod -n 1

schedule:
	zappa schedule prod

status:
	zappa status prod

tail:
	zappa tail prod --since 15m

undeploy:
	zappa undeploy prod --remove-logs

unschedule:
	zappa unschedule prod

update:
	zappa update prod

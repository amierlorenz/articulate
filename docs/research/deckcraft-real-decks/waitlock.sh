#!/bin/sh
end=$(($(date +%s)+43200))
while [ $(date +%s) -lt $end ]; do
  ioreg -n Root -d1 | grep -q '"CGSSessionScreenIsLocked"=Yes' && { sleep 30; exec /opt/homebrew/opt/python@3.14/bin/python3.14 -I $JOB/la/run.py $JOB locked; }
  sleep 5
done

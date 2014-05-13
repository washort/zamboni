# For use in Vagrant development environments to launch a
# Zamboni+Fireplace server.
PIDFILE="/home/vagrant/mkt.pid"
if [ -e $PIDFILE ]; then
    kill -9 `cat $PIDFILE`
fi
export FIREPLACE_ROOT=/home/vagrant/fireplace
export PYTHONPATH=/home/vagrant/project:$PYTHONPATH
twistd --pidfile=$PIDFILE --logfile=/home/vagrant/mkt.log \
    -y /home/vagrant/project/mkt.tac

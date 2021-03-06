#!/usr/bin/env python

# Plotsignal plots data from the FieldTrip buffer. Currently it also includes user-defined filtering
#
# Plotsignal is part of the EEGsynth project (https://github.com/eegsynth/eegsynth)
#
# Copyright (C) 2017 EEGsynth project
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from pyqtgraph.Qt import QtGui, QtCore
from scipy.interpolate import interp1d
from scipy.signal import butter, lfilter
import ConfigParser # this is version 2.x specific, on version 3.x it is called "configparser" and has a different API
import redis
import argparse
import numpy as np
import os
import pyqtgraph as pg
import sys
import time
from scipy.signal import butter, lfilter, detrend
from scipy.interpolate import interp1d
from scipy.fftpack import fft, fftfreq

if hasattr(sys, 'frozen'):
    basis = sys.executable
elif sys.argv[0]!='':
    basis = sys.argv[0]
else:
    basis = './'
installed_folder = os.path.split(basis)[0]

# eegsynth/lib contains shared modules
sys.path.insert(0, os.path.join(installed_folder, '../../lib'))
import EEGsynth
import FieldTrip

parser = argparse.ArgumentParser()
parser.add_argument("-i", "--inifile", default=os.path.join(installed_folder, os.path.splitext(os.path.basename(__file__))[0] + '.ini'), help="optional name of the configuration file")
args = parser.parse_args()

config = ConfigParser.ConfigParser()
config.read(args.inifile)

try:
    r = redis.StrictRedis(host=config.get('redis','hostname'), port=config.getint('redis','port'), db=0)
    response = r.client_list()
except redis.ConnectionError:
    print "Error: cannot connect to redis server"
    exit()

# combine the patching from the configuration file and Redis
patch = EEGsynth.patch(config, r)
del config

# this determines how much debugging information gets printed
debug = patch.getint('general','debug')

def butter_bandpass(lowcut, highcut, fs, order=9):
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    b, a = butter(order, [low, high], btype='band')
    return b, a

def butter_bandpass_filter(data, lowcut, highcut, fs, order=9):
    b, a = butter_bandpass(lowcut, highcut, fs, order=order)
    y = lfilter(b, a, data)
    return y

def butter_lowpass(lowcut, fs, order=9):
    nyq = 0.5 * fs
    low = lowcut / nyq
    b, a = butter(order, low, btype='low')
    return b, a

def butter_lowpass_filter(data, lowcut, fs, order=9):
    b, a = butter_lowpass(lowcut, fs, order=order)
    y    = lfilter(b, a, data)
    return y

try:
    ftc_host = patch.getstring('fieldtrip', 'hostname')
    ftc_port = patch.getint('fieldtrip', 'port')
    if debug > 0:
        print 'Trying to connect to buffer on %s:%i ...' % (ftc_host, ftc_port)
    ft_input = FieldTrip.Client()
    ft_input.connect(ftc_host, ftc_port)
    if debug > 0:
        print "Connected to input FieldTrip buffer"
except:
    print "Error: cannot connect to input FieldTrip buffer"
    exit()

hdr_input = None
while hdr_input is None:
    if debug > 0:
        print "Waiting for data to arrive..."
        hdr_input = ft_input.getHeader()
    time.sleep(0.2)

print "Data arrived"

# read variables from ini/redis
chanlist = patch.getstring('arguments', 'channels').split(",")
chanarray = np.array(chanlist)
for i in range(len(chanarray)):
    chanarray[i] = int(chanarray[i]) - 1                # since python using indexing from 0 instead of 1

chan_nrs    = len(chanlist)
window      = patch.getfloat('arguments', 'window')     # in seconds
window      = int(round(window*hdr_input.fSample))      # in samples
clipsize    = patch.getfloat('arguments', 'clipsize')   # in seconds
clipsize    = int(round(clipsize*hdr_input.fSample))    # in samples
stepsize    = patch.getfloat('arguments', 'stepsize')   # in seconds
winx        = patch.getfloat('display', 'xpos')
winy        = patch.getfloat('display', 'ypos')
winwidth    = patch.getfloat('display', 'width')
winheight   = patch.getfloat('display', 'height')
lrate       = patch.getfloat('arguments', 'learning_rate')

# initialize graphical window
app = QtGui.QApplication([])
win = pg.GraphicsWindow(title="EEGsynth")
win.setWindowTitle('EEGsynth')
win.setGeometry(winx, winy, winwidth, winheight)

# Enable antialiasing for prettier plots
pg.setConfigOptions(antialias=True)

# Initialize variables
timeplot  = []
curve     = []
curvemax  = []

# Create panels (timecourse and spectrum) for each channel
for ichan in range(chan_nrs):

    channr = int(chanarray[ichan]) + 1
    timeplot.append(win.addPlot(title="%s%s" % ('Channel ', channr)))
    timeplot[ichan].setLabel('left', text='Amplitude')
    timeplot[ichan].setLabel('bottom', text='Time (s)')
    curve.append(timeplot[ichan].plot(pen='w'))
    win.nextRow()

    # initialize as list
    curvemax.append(0)

def update():
   global curvemax, counter

   # get last data
   last_index = ft_input.getHeader().nSamples
   begsample = (last_index-window)
   endsample = (last_index-1)
   data = ft_input.getData([begsample, endsample])
   if debug > 0:
       print "reading from sample %d to %d" % (begsample, endsample)

   # demean data before filtering to reduce edge artefacts and center timecourse
   data = data - np.sum(data, axis=0)/float(len(data))
   data = detrend(data, axis=0)

   # user-defined filtering
   arguments_freqrange = patch.getstring('arguments', 'bandpass').split("-")
   arguments_freqrange = [float(s) for s in arguments_freqrange]
   data = butter_bandpass_filter(data.T, arguments_freqrange[0], arguments_freqrange[1], int(hdr_input.fSample), 9).T[clipsize:-clipsize]

   for ichan in range(chan_nrs):

        channr = int(chanarray[ichan])

        # time axis
        timeaxis = np.linspace(0,len(data)/hdr_input.fSample,len(data))

        # update timecourses
        curve[ichan].setData(timeaxis,data[:,channr])

        # adapt the vertical scale to the running mean of max
        curvemax[ichan] = float(curvemax[ichan])  * (1-lrate) + lrate * max(abs(data[:,ichan]))
        timeplot[ichan].setYRange(-curvemax[ichan], curvemax[ichan])

# Set timer for update
timer = QtCore.QTimer()
timer.timeout.connect(update)
timer.setInterval(10)                   # timeout in milliseconds
timer.start(int(round(stepsize*1000)))  # in milliseconds

# Wait until there is enough data
begsample = -1
while begsample<0:
    hdr_input = ft_input.getHeader()
    begsample = int(hdr_input.nSamples - window)

# Start
QtGui.QApplication.instance().exec_()

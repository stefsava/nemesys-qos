#!/usr/bin/env python
# -*- coding: utf-8 -*-
# generated by wxGlade 0.6.3 on Wed Apr 11 17:48:58 2012

from ConfigParser import ConfigParser, NoOptionError
from client import Client
from datetime import datetime
from isp import Isp
from measure import Measure
from optparse import OptionParser
from os import path
from profile import Profile
from server import Server
from sys import platform
from sysmonitor import checkset, RES_OS, RES_CPU, RES_RAM, RES_WIFI, RES_TRAFFIC, RES_HOSTS
from task import Task
from tester import Tester
from threading import Thread, Event
from time import sleep
from timeNtp import timestampNtp
from urlparse import urlparse
from xmlutils import xml2task
from usbkey import check_usb, move_on_key
from logger import logging
from collections import deque
import hashlib
import httputils
import paths
import ping
import sysmonitor
import time
import wx
from prospect import Prospect

__version__ = '1.0.2'

#Data di scadenza
dead_date = 20120930

# Tempo di attesa tra una misura e la successiva in caso di misura fallita
TIME_LAG = 5
DOWN = 'down'
UP = 'up'
# Soglia per il rapporto tra traffico 'spurio' e traffico totale
TH_TRAFFIC = 0.1
TH_TRAFFIC_INV = 0.9
# Soglia per numero di pacchetti persi
TH_PACKETDROP = 0.05
MAX_TEST_ERROR = 0

TOTAL_STEPS = 12

logger = logging.getLogger()

def sleeper():
    sleep(.001)
    return 1 # don't forget this otherwise the timeout will be removed

class OptionParser(OptionParser):

  def check_required(self, opt):
    option = self.get_option(opt)
    if getattr(self.values, option.dest) is None:
      self.error('%s option not supplied' % option)

class _Checker(Thread):

  def __init__(self, gui, type = 'check', checkable_set = set([RES_OS, RES_CPU, RES_RAM, RES_WIFI, RES_HOSTS, RES_TRAFFIC])):
    Thread.__init__(self)
    
    self._gui = gui
    self._type = type
    self._checkable_set = checkable_set
    
    self._events = {}
    self._results = {}
    self._cycle = Event()
    self._results_flag = Event()
    self._traffic_wait_hosts = Event()
    self._software_ok = False

  def run(self):

    self._events.clear()
    self._results.clear()
    self._cycle.set()
  
    while (self._cycle.isSet()):
      self._results_flag.clear()
      
      if (self._type != 'tester'):
        self._software_ok = self._check_software()
      else:
        self._software_ok = True

      if (self._software_ok or self._type == 'software'):
        self._traffic_wait_hosts.clear()
        
        for res in self._checkable_set:
          res_flag = Event()
          self._events[res] = res_flag
          self._events[res].clear()
          res_check = Thread(target=self._check_resource, args=(res,))
          res_check.start()
            
        while (len(self._events) > 0):
          for res in self._events.keys():
            if self._events[res].isSet():
              del self._events[res]
              if (self._type == 'tester'):
                message_flag = False
              else:
                message_flag = True
              wx.CallAfter(self._gui.set_resource_info, res, self._results[res], message_flag)
              
              
        self._results_flag.set()
        
        if (self._type != 'tester'):
          self._cycle.clear()
          
    if (self._software_ok and self._type == 'check'):
      wx.CallAfter(self._gui._after_check)
  
  def stop(self):
    self._cycle.clear()
    
  def set_check(self, checkable_set = set([RES_OS, RES_CPU, RES_RAM, RES_WIFI, RES_HOSTS, RES_TRAFFIC])):
    self._checkable_set = checkable_set
  
  def _check_resource(self, resource):
    if resource == RES_TRAFFIC:
      self._traffic_wait_hosts.wait()
    result = checkset(set([resource]))
    self._results.update(result)
    self._events[resource].set()
    if resource == RES_HOSTS:
      self._traffic_wait_hosts.set()
    
  def get_results(self):
    self._results_flag.wait()
    self._results_flag.clear()
    if (self._type == 'software'):
      results = self._software_ok
    else:
      results = self._results
    return results
    
  def _check_software(self):
    check = False
    if (self._deadline()):
      self._cycle.clear()
      logger.debug('Verifica della scadenza del software fallita')
      wx.CallAfter(self._gui._update_messages, "Questa copia di Ne.Me.Sys Speedtest risulta scaduta. Si consiglia di disinstallare il software.", 'red')
    elif (not check_usb()):
      self._cycle.clear()
      logger.debug('Verifica della presenza della chiave USB fallita')
      wx.CallAfter(self._gui._update_messages, "Per l'utilizzo di questo software occorre disporre della opportuna chiave USB. Inserire la chiave nel computer e riavviare il programma.", 'red')
    elif (self._new_version_available()):
      self._cycle.clear()
      logger.debug('Verifica della presenza di nuove versioni del software')
      wx.CallAfter(self._gui._update_messages, "E' disponibile per il download una nuova versione del software!", 'red')
    else:
      check = True
    return check
    
  def _deadline(self):
    this_date = int(getdate().strftime('%Y%m%d'))
    #logger.debug('%d > %d = %s' % (this_date,dead_date,(this_date > dead_date)))
    return (this_date > dead_date)
    
  def _new_version_available(self):
    (options, args, md5conf) = parse()
    httptimeout = options.httptimeout
    
    new_version = False

    url = urlparse("https://www.misurainternet.it/nemesys_speedtest_check.php")
    connection = httputils.getverifiedconnection(url = url, certificate = None, timeout = httptimeout)

    try:
      connection.request('GET', '%s?speedtest=true&version=%s' % (url.path, __version__))
      data = connection.getresponse().read()
      #logger.debug(data)
      if (data == "NEWVERSION"):
        new_version = True
    except Exception as e:
      logger.error('Impossibile controllare per nuove versioni. Errore: %s.' % e)
      new_version = False

    return new_version


class _Tester(Thread):

  def __init__(self, gui):
    Thread.__init__(self)
    paths.check_paths()
    self._outbox = paths.OUTBOX_DAY_DIR
    self._prospect = Prospect()

    self._step = 0
    self._gui = gui
    self._checker = _Checker(self._gui, 'tester')
    self._checker.start()
    
    (options, args, md5conf) = parse()

    self._client = getclient(options)
    self._scheduler = options.scheduler
    self._tasktimeout = options.tasktimeout
    self._testtimeout = options.testtimeout
    self._httptimeout = options.httptimeout
    self._md5conf = md5conf

    self._running = True

  def join(self, timeout = None):
    logger.debug("Richiesta di close")
    #wx.CallAfter(self._gui._update_messages, "Attendere la chiusura del programma...")
    self._running = False

  def _test_gating(self, test, testtype):
    '''
    Funzione per l'analisi del contabit ed eventuale gating dei risultati del test
    '''
    stats = test.counter_stats
    logger.debug('Valori di test: %s' % stats)
    continue_testing = False

    logger.debug('Analisi della percentuale dei pacchetti persi')
    packet_drop = stats.packet_drop
    packet_tot = stats.packet_tot_all
    if (packet_tot > 0):
      packet_ratio = float(packet_drop) / float(packet_tot)
      logger.debug('Percentuale di pacchetti persi: %.2f%%' % (packet_ratio * 100))
      if (packet_tot > 0 and packet_ratio > TH_PACKETDROP):
        info = 'Eccessiva presenza di traffico di rete, impossibile analizzare i dati di test'
        wx.CallAfter(self._gui.set_resource_info, RES_TRAFFIC, {'status': False, 'info': info, 'value': None})
        return continue_testing

    else:
      info = 'Errore durante la misura, impossibile analizzare i dati di test'
      wx.CallAfter(self._gui.set_resource_info, RES_TRAFFIC, {'status': False, 'info': info, 'value': None})
      return continue_testing

    if (testtype == DOWN):
      byte_nem = stats.payload_down_nem_net
      byte_all = byte_nem + stats.byte_down_oth_net
      packet_nem_inv = stats.packet_up_nem_net
      packet_all_inv = packet_nem_inv + stats.packet_up_oth_net
    else:
      byte_nem = stats.payload_up_nem_net
      byte_all = byte_nem + stats.byte_up_oth_net
      packet_nem_inv = stats.packet_down_nem_net
      packet_all_inv = packet_nem_inv + stats.packet_down_oth_net

    logger.debug('Analisi dei rapporti di traffico')
    if byte_all > 0 and packet_all_inv > 0:
      traffic_ratio = float(byte_all - byte_nem) / float(byte_all)
      packet_ratio_inv = float(packet_all_inv - packet_nem_inv) / float(packet_all_inv)
      value1 = "%.2f%%" % (traffic_ratio * 100)
      value2 = "%.2f%%" % (packet_ratio_inv * 100)
      logger.info('kbyte_nem: %.1f; kbyte_all %.1f; packet_nem_inv: %d; packet_all_inv: %d' % (byte_nem / 1024.0, byte_all / 1024.0, packet_nem_inv, packet_all_inv))
      logger.debug('Percentuale di traffico spurio: %.2f%%/%.2f%%' % (traffic_ratio * 100, packet_ratio_inv * 100))
      if traffic_ratio < 0:
        wx.CallAfter(self._gui._update_messages, 'Errore durante la verifica del traffico di misura: impossibile salvare i dati.', 'red')
        return continue_testing
      elif traffic_ratio < TH_TRAFFIC and packet_ratio_inv < TH_TRAFFIC_INV:
        # Dato da salvare sulla misura
        # test.bytes = byte_all
        info = 'Traffico internet non legato alla misura: percentuali %s/%s' % (value1, value2)
        wx.CallAfter(self._gui.set_resource_info, RES_TRAFFIC, {'status': True, 'info': info, 'value': value1}, False)
        return True
      else:
        info = 'Eccessiva presenza di traffico internet non legato alla misura: percentuali %s/%s' % (value1, value2)
        wx.CallAfter(self._gui.set_resource_info, RES_TRAFFIC, {'status': False, 'info': info, 'value': value1})
        return continue_testing
    else:
      info = 'Errore durante la misura, impossibile analizzare i dati di test'
      wx.CallAfter(self._gui.set_resource_info, RES_TRAFFIC, {'status': False, 'info': info, 'value': value1})
      return continue_testing

    return True

  def _get_bandwith(self, test):

    if test.value > 0:
      return int(round(test.bytes * 8 / test.value))
    else:
      raise Exception("Errore durante la valutazione del test")

  def _update_gauge(self):
    self._step += 1
    wx.CallAfter(self._gui.update_gauge, self._step)

  def _get_server(self, servers = set([Server('NAMEX', '193.104.137.133', 'NAP di Roma'), Server('MIX', '193.104.137.4', 'NAP di Milano')])):

    maxREP = 3
    best_delay = 8000
    best_server = None
    RTT = {}
    
    wx.CallAfter(self._gui._update_messages, "Scelta del server di misura in corso")

    for server in servers:
      RTT[server] = best_delay
    
    for repeat in range(maxREP):
      for server in servers:
        try:
          delay = ping.do_one("%s" % server.ip, 1)
          if (delay < RTT[server]):
            RTT[server] = delay
          if (delay < best_delay):
            best_delay = delay
            best_server = server
        except Exception as e:
          logger.debug('Errore durante il ping dell\'host %s: %s' % (server.ip, e))
          pass
         
    if best_server != None:
      for server in servers:
        if (RTT[server] != 8000):
          wx.CallAfter(self._gui._update_messages, "Distanza dal %s: %.1f ms" % (server.name, RTT[server] * 1000), 'blue')
        else:
          wx.CallAfter(self._gui._update_messages, "Distanza dal %s: TimeOut" % (server.name), 'blue')
      wx.CallAfter(self._gui._update_messages, "Scelto il server di misura %s" % best_server.name)
      # return best_server
    else:
      wx.CallAfter(self._gui._update_messages, "Impossibile eseguire i test poiche' i server risultano irragiungibili da questa linea. Contattare l'helpdesk del progetto Misurainternet per avere informazioni sulla risoluzione del problema.", 'red')
      
    return best_server

  def _do_ftp_test(self, tester, type, task):
    i = 1
    test_number = 0

    best_band = 0
    best_test = None
    best_prof = {}

    if type == DOWN:
      test_number = task.download
    elif type == UP:
      test_number = task.upload

    while (i <= test_number and self._running):

      wx.CallAfter(self._gui._update_messages, "Test %d di %d di FTP %s" % (i, test_number, type), 'blue')

      prof = {}
      prof = self._checker.get_results()

      # Esecuzione del test
      test = None
      error = 0
      while (error < MAX_TEST_ERROR or test == None):
        sleep(1)
        try:
          if type == DOWN:
            logger.info("Esecuzione di un test Donwload FTP")
            test = tester.testftpdown(self._client.profile.download * task.multiplier * 1000 / 8, task.ftpdownpath)
          elif type == UP:
            logger.info("Esecuzione di un test Upload FTP")
            test = tester.testftpup(self._client.profile.upload * task.multiplier * 1000 / 8, task.ftpuppath)
          else:
            logger.warn("Tipo di test da effettuare non definito!")
        except Exception as e:
          error = error + 1
          test = None
          logger.error("Errore durante l'esecuzione di un test: %s" % e)
          wx.CallAfter(self._gui._update_messages, "Errore durante l'esecuzione di un test: %s" % e, 'red')
          wx.CallAfter(self._gui._update_messages, "Ripresa del test tra %d secondi" % TIME_LAG)
          sleep(TIME_LAG)

      if test != None:
        bandwidth = self._get_bandwith(test)

        if type == DOWN:
          self._client.profile.download = min(bandwidth, (40000 / 8) * 10)
          task.update_ftpdownpath(bandwidth)
        elif type == UP:
          self._client.profile.upload = min(bandwidth, (40000 / 8) * 10)
        else:
          logger.warn("Tipo di test effettuato non definito!")

        #wx.CallAfter(self._gui._update_messages, "Fine del test %d di %d di FTP %s" % (i, test_number, type), 'blue')

        if i > 1:
          # Analisi da contabit
          if (self._test_gating(test, type)):
            i = i + 1
            logger.debug("| Test Bandwidth: %s | Actual Best: %s |" % (bandwidth, best_band))
            if bandwidth > best_band:
              best_band = bandwidth
              best_test = test
              best_prof = prof
            self._update_gauge()
        else:
          i = i + 1

      else:
        raise Exception("errore durante i test, la misurazione non puo' essere completata")

    return (best_test, best_prof)

  def run(self):

    logger.debug('Inizio dei test di misura')
    wx.CallAfter(self._gui._update_messages, "Inizio dei test di misura")
    self._update_gauge()

    # Profilazione
    profiler = {}
    profiler = self._checker.get_results()
    self._checker.set_check(set([RES_CPU, RES_RAM, RES_WIFI]))

    # TODO Il server deve essere indicato dal backend che è a conoscenza dell'occupazione della banda!

    # TODO Rimuovere dopo aver sistemato il backend
    task = None
    sleep(1)
    server = self._get_server()
    if server != None:
      # Scaricamento del task dallo scheduler
      task = self._download_task(server)
      self._update_gauge()
      task = Task(0, '2010-01-01 10:01:00', server, '/download/1000.rnd', 'upload/1000.rnd', 4, 4, 10, 4, 4, 0, True)

    if task != None:

      try:
        start = datetime.fromtimestamp(timestampNtp())

        ip = sysmonitor.getIp()
        t = Tester(if_ip = ip, host = task.server, timeout = self._testtimeout,
                   username = self._client.username, password = self._client.password)

        id = start.strftime('%y%m%d%H%M')
        m = Measure(id, task.server, self._client, __version__, start.isoformat())

        # Testa gli ftp down
        (test, prof) = self._do_ftp_test(t, DOWN, task)
        profiler.update(prof)
        m.savetest(test, profiler)
        wx.CallAfter(self._gui._update_messages, "Elaborazione dei dati")
        if (move_on_key()):
          wx.CallAfter(self._gui._update_messages, "Download bandwith %s kbps" % self._get_bandwith(test), 'green')
          wx.CallAfter(self._gui._update_down, self._get_bandwith(test))
        else:
          raise Exception("chiave USB mancante")

        # Testa gli ftp up
        (test, prof) = self._do_ftp_test(t, UP, task)
        profiler.update(prof)
        m.savetest(test, profiler)
        wx.CallAfter(self._gui._update_messages, "Elaborazione dei dati")
        if (move_on_key()):
          wx.CallAfter(self._gui._update_messages, "Upload bandwith %s kbps" % self._get_bandwith(test), 'green')
          wx.CallAfter(self._gui._update_up, self._get_bandwith(test))
        else:
          raise Exception("chiave USB mancante")


        # Testa i ping
        i = 1

        while (i <= task.ping and self._running):

          wx.CallAfter(self._gui._update_messages, "Test %d di %d di ping." % (i, task.ping), 'blue')
          test = t.testping()
          self._update_gauge()
          #wx.CallAfter(self._gui._update_messages, "Fine del test %d di %d di ping." % (i, task.ping), 'blue')

          if ((i + 2) % task.nicmp == 0):
            sleep(task.delay)
            prof = {}
            prof = self._checker.get_results()

          i = i + 1

        # Salvataggio dell'ultima misura
        profiler.update(prof)
        m.savetest(test, profiler)
        self._save_measure(m)
        self._prospect.save_measure(m)
        wx.CallAfter(self._gui._update_messages, "Elaborazione dei dati")
        if (move_on_key()):
          wx.CallAfter(self._gui._update_messages, "Tempo di risposta del server %s ms" % int(round(test.value)), 'green')
          wx.CallAfter(self._gui._update_ping, int(round(test.value)))
        else:
          raise Exception("chiave USB mancante")

      except Exception as e:
        logger.warning('Misura sospesa per eccezione: %s.' % e)
        wx.CallAfter(self._gui._update_messages, 'Misura sospesa per errore: %s.' % e, 'red')

      # Stop
      #sleep(TIME_LAG)
      
    self._checker.stop()
    wx.CallAfter(self._gui.stop)
    self.join()

  def _save_measure(self, measure):
    # Salva il file con le misure
    sec = datetime.fromtimestamp(timestampNtp()).strftime('%S')
    f = open('%s/measure_%s%s.xml' % (self._outbox, measure.id, sec), 'w')
    f.write(str(measure))

    # Aggiungi la data di fine in fondo al file
    f.write('\n<!-- [finished] %s -->' % datetime.fromtimestamp(timestampNtp()).isoformat())
    f.close()

  # Scarica il prossimo task dallo scheduler
  def _download_task(self, server):

    url = urlparse(self._scheduler)
    connection = httputils.getverifiedconnection(url = url, certificate = None, timeout = self._httptimeout)

    try:
      connection.request('GET', '%s?clientid=%s&version=%s&confid=%s&server=%s' % (url.path, self._client.id, __version__, self._md5conf, server.ip))
      data = connection.getresponse().read()
    except Exception as e:
      logger.error('Impossibile scaricare lo scheduling. Errore: %s.' % e)
      return None

    return xml2task(data)

class Frame(wx.Frame):
    def __init__(self, *args, **kwds):
        self._stream = deque([], maxlen = 800)
        self._stream_flag = Event()
        
        self._tester = None
        self._checker = None
        self._button_play = False
        self._button_check = False

        # begin wxGlade: Frame.__init__
        wx.Frame.__init__(self, *args, **kwds)

        self.sizer_3_staticbox = wx.StaticBox(self, -1, "Messaggi")
        self.bitmap_button_play = wx.BitmapButton(self, -1, wx.Bitmap(path.join(paths.ICONS, u"play.png"), wx.BITMAP_TYPE_ANY))
        self.bitmap_button_check = wx.BitmapButton(self, -1, wx.Bitmap(path.join(paths.ICONS, u"check.png"), wx.BITMAP_TYPE_ANY))
        self.bitmap_5 = wx.StaticBitmap(self, -1, wx.Bitmap(path.join(paths.ICONS, u"logo_nemesys.png"), wx.BITMAP_TYPE_ANY))
        self.label_5 = wx.StaticText(self, -1, "", style = wx.ALIGN_CENTRE)
        self.label_6 = wx.StaticText(self, -1, "Ne.Me.Sys.", style = wx.ALIGN_CENTRE)
        self.bitmap_cpu = wx.StaticBitmap(self, -1, wx.Bitmap(path.join(paths.ICONS, u"%s_gray.png" % RES_CPU.lower()), wx.BITMAP_TYPE_ANY))
        self.bitmap_ram = wx.StaticBitmap(self, -1, wx.Bitmap(path.join(paths.ICONS, u"%s_gray.png" % RES_RAM.lower()), wx.BITMAP_TYPE_ANY))
        self.bitmap_wifi = wx.StaticBitmap(self, -1, wx.Bitmap(path.join(paths.ICONS, u"%s_gray.png" % RES_WIFI.lower()), wx.BITMAP_TYPE_ANY))
        self.bitmap_hosts = wx.StaticBitmap(self, -1, wx.Bitmap(path.join(paths.ICONS, u"%s_gray.png" % RES_HOSTS.lower()), wx.BITMAP_TYPE_ANY))
        self.bitmap_traffic = wx.StaticBitmap(self, -1, wx.Bitmap(path.join(paths.ICONS, u"%s_gray.png" % RES_TRAFFIC.lower()), wx.BITMAP_TYPE_ANY))
        self.label_cpu = wx.StaticText(self, -1, "%s\n- - - -" % RES_CPU, style = wx.ALIGN_CENTRE)
        self.label_ram = wx.StaticText(self, -1, "%s\n- - - -" % RES_RAM, style = wx.ALIGN_CENTRE)
        self.label_wifi = wx.StaticText(self, -1, "%s\n- - - -" % RES_WIFI, style = wx.ALIGN_CENTRE)
        self.label_hosts = wx.StaticText(self, -1, "%s\n- - - -" % RES_HOSTS, style = wx.ALIGN_CENTRE)
        self.label_traffic = wx.StaticText(self, -1, "%s\n- - - -" % RES_TRAFFIC, style = wx.ALIGN_CENTRE)
        self.gauge_1 = wx.Gauge(self, -1, TOTAL_STEPS, style = wx.GA_HORIZONTAL | wx.GA_SMOOTH)
        self.label_r_1 = wx.StaticText(self, -1, "Ping", style = wx.ALIGN_CENTRE)
        self.label_r_2 = wx.StaticText(self, -1, "Download", style = wx.ALIGN_CENTRE)
        self.label_r_3 = wx.StaticText(self, -1, "Upload", style = wx.ALIGN_CENTRE)
        self.label_rr_ping = wx.StaticText(self, -1, "- - - -", style = wx.ALIGN_CENTRE)
        self.label_rr_down = wx.StaticText(self, -1, "- - - -", style = wx.ALIGN_CENTRE)
        self.label_rr_up = wx.StaticText(self, -1, "- - - -", style = wx.ALIGN_CENTRE)
        self.messages_area = wx.TextCtrl(self, -1, "Ne.Me.Sys. Speedtest v.%s" % __version__, style = wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.TE_WORDWRAP)
        self.grid_sizer_1 = wx.GridSizer(2, 5, 0, 0)
        self.grid_sizer_2 = wx.GridSizer(2, 3, 0, 0)

        self.__set_properties()
        self.__do_layout()

        #self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_BUTTON, self._play, self.bitmap_button_play)
        self.Bind(wx.EVT_BUTTON, self._check, self.bitmap_button_check)
        # end wxGlade

    def _on_close_event(self, event):

      logger.debug("Richiesta di close")
      #if (self._tester and self._tester != None):
      #  self._tester.join()
      self.Destroy()

    def __set_properties(self):
        # begin wxGlade: Frame.__set_properties
        self.SetTitle("Ne.Me.Sys Speedtest")
        self.SetSize((720, 440))
        self.bitmap_button_play.SetMinSize((120, 120))
        self.bitmap_button_check.SetMinSize((40, 120))
        self.bitmap_5.SetMinSize((95, 70))
        #self.label_5.SetFont(wx.Font(18, wx.ROMAN, wx.NORMAL, wx.NORMAL, 0, ""))
        self.label_6.SetFont(wx.Font(14, wx.ROMAN, wx.ITALIC, wx.NORMAL, 0, ""))
        self.bitmap_cpu.SetMinSize((60, 60))
        self.bitmap_ram.SetMinSize((60, 60))
        self.bitmap_wifi.SetMinSize((60, 60))
        self.bitmap_hosts.SetMinSize((60, 60))
        self.bitmap_traffic.SetMinSize((60, 60))
        self.gauge_1.SetMinSize((700, 24))
        self.label_rr_ping.SetFont(wx.Font(12, wx.SWISS, wx.NORMAL, wx.BOLD, 0, ""))
        self.label_rr_down.SetFont(wx.Font(12, wx.SWISS, wx.NORMAL, wx.BOLD, 0, ""))
        self.label_rr_up.SetFont(wx.Font(12, wx.SWISS, wx.NORMAL, wx.BOLD, 0, ""))

        self.messages_area.SetMinSize((700, 150))
        self.messages_area.SetFont(wx.Font(12, wx.SWISS, wx.NORMAL, wx.NORMAL, 0, ""))
        self.grid_sizer_2.SetMinSize((700, 60))

        #self.SetBackgroundColour(wx.SystemSettings_GetColour(wx.SYS_COLOUR_WINDOW))
        self.SetBackgroundColour(wx.Colour(242, 242, 242))

        # end wxGlade

    def __do_layout(self):
        # begin wxGlade: Frame.__do_layout   
        self.grid_sizer_1.Add(self.bitmap_cpu, 0, wx.LEFT | wx.RIGHT | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 12)
        self.grid_sizer_1.Add(self.bitmap_ram, 0, wx.LEFT | wx.RIGHT | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 12)
        self.grid_sizer_1.Add(self.bitmap_wifi, 0, wx.LEFT | wx.RIGHT | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 12)
        self.grid_sizer_1.Add(self.bitmap_hosts, 0, wx.LEFT | wx.RIGHT | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 12)
        self.grid_sizer_1.Add(self.bitmap_traffic, 0, wx.LEFT | wx.RIGHT | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 12)
        self.grid_sizer_1.Add(self.label_cpu, 0, wx.LEFT | wx.RIGHT | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 2)
        self.grid_sizer_1.Add(self.label_ram, 0, wx.LEFT | wx.RIGHT | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 2)
        self.grid_sizer_1.Add(self.label_wifi, 0, wx.LEFT | wx.RIGHT | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 2)
        self.grid_sizer_1.Add(self.label_hosts, 0, wx.LEFT | wx.RIGHT | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 2)
        self.grid_sizer_1.Add(self.label_traffic, 0, wx.LEFT | wx.RIGHT | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 2)

        self.grid_sizer_2.Add(self.label_r_1, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 2)
        self.grid_sizer_2.Add(self.label_r_2, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 2)
        self.grid_sizer_2.Add(self.label_r_3, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 2)
        self.grid_sizer_2.Add(self.label_rr_ping, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_TOP, 2)
        self.grid_sizer_2.Add(self.label_rr_down, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_TOP, 2)
        self.grid_sizer_2.Add(self.label_rr_up, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_TOP, 2)

        sizer_1 = wx.BoxSizer(wx.VERTICAL)
        sizer_2 = wx.BoxSizer(wx.HORIZONTAL)
        sizer_4 = wx.BoxSizer(wx.VERTICAL)
        sizer_6 = wx.StaticBoxSizer(self.sizer_3_staticbox, wx.VERTICAL)

        sizer_4.Add(self.bitmap_5, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 2)
        #sizer_4.Add(self.label_5, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 2)
        sizer_4.Add(self.label_6, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 2)

        sizer_2.Add(self.bitmap_button_play, 0, wx.LEFT | wx.ALIGN_RIGHT | wx.ALIGN_TOP, 4)
        sizer_2.Add(self.bitmap_button_check, 0, wx.LEFT | wx.ALIGN_RIGHT | wx.ALIGN_TOP, 4)
        sizer_2.Add(self.grid_sizer_1, 0, wx.RIGHT | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 10)
        sizer_2.Add(sizer_4, 0, wx.RIGHT | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 4)

        sizer_6.Add(self.messages_area, 0, wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 0)

        sizer_1.Add(sizer_2, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 6)
        sizer_1.Add(self.gauge_1, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 0)
        sizer_1.Add(sizer_6, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 6)
        sizer_1.Add(self.grid_sizer_2, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_CENTER_VERTICAL, 0)

        self.SetSizer(sizer_1)
        self.Layout()
        # end wxGlade

        self._check(None)

    def _play(self, event):
      self._button_play = True
      self._check(None)
      #self.bitmap_button_play.SetBitmapLabel(wx.Bitmap(path.join(paths.ICONS, u"stop.png")))

    def stop(self):
      #self.bitmap_button_play.SetBitmapLabel(wx.Bitmap(path.join(paths.ICONS, u"play.png")))
      self._checker = _Checker(self, 'software', set())
      self._checker.start()
      self._check_software = self._checker.get_results()
      if (self._check_software):
        self._enable_button()
        self._update_messages("Sistema pronto per una nuova misura")
            
      self.update_gauge(0)
      
    def _check(self, event):
      logger.debug('Profilazione dello stato del sistema di misura.')
      self._update_messages("Profilazione dello stato del sistema di misura.")
      
      self._button_check = True
      self.bitmap_button_play.Disable()
      self.bitmap_button_check.Disable()
      self._reset_info()
      self._checker = _Checker(self)
      self._checker.start()
      
    def _after_check(self):
      if (self._button_play):
        self._button_play = False
        self._button_check = False
        self._tester = _Tester(self)
        self._tester.start()
      else:
        move_on_key()
        self._button_check = False
        self._update_messages("Profilazione terminata")
        self._enable_button()

    def _enable_button(self):
      self.bitmap_button_play.Enable()
      self.bitmap_button_check.Enable()
      
    def _update_down(self, downwidth):
      self.label_rr_down.SetLabel("%d kbps" % downwidth)
      self.Layout()

    def _update_up(self, upwidth):
      self.label_rr_up.SetLabel("%d kbps" % upwidth)
      self.Layout()

    def _update_ping(self, rtt):
      self.label_rr_ping.SetLabel("%d ms" % rtt)
      self.Layout()

    def _reset_info(self):

      checkable_set = set([RES_CPU, RES_RAM, RES_WIFI, RES_HOSTS, RES_TRAFFIC])

      for resource in checkable_set:
        self.set_resource_info(resource, {'status': None, 'info': None, 'value': None})

      self.label_rr_down.SetLabel("- - - -")
      self.label_rr_up.SetLabel("- - - -")
      self.label_rr_ping.SetLabel("- - - -")

      self.messages_area.Clear()
      self.update_gauge(0)
      self.Layout()

    def update_gauge(self, value):
      # logger.debug("Gauge value %d" % value)
      self.gauge_1.SetValue(value)

      
    def set_resource_info(self, resource, info, message_flag = True):
      res_bitmap = None
      res_label = None

      if info['status'] == None:
        color = 'gray'
      elif info['status'] == True:
        color = 'green'
      else:
        color = 'red'

      if resource == RES_CPU:
        res_bitmap = self.bitmap_cpu
        res_label = self.label_cpu
      elif resource == RES_RAM:
        res_bitmap = self.bitmap_ram
        res_label = self.label_ram
      elif resource == RES_WIFI:
        res_bitmap = self.bitmap_wifi
        res_label = self.label_wifi
      elif resource == RES_HOSTS:
        res_bitmap = self.bitmap_hosts
        res_label = self.label_hosts
      elif resource == RES_TRAFFIC:
        res_bitmap = self.bitmap_traffic
        res_label = self.label_traffic

      if (res_bitmap != None):
        res_bitmap.SetBitmap(wx.Bitmap(path.join(paths.ICONS, u"%s_%s.png" % (resource.lower(), color))))

      if (res_label != None):
        if (info['value'] != None):
          if resource == RES_CPU or resource == RES_RAM:
            res_label.SetLabel("%s\n%.1f%%" % (resource, float(info['value'])))
          else:
            res_label.SetLabel("%s\n%s" % (resource, info['value']))
        else:
          res_label.SetLabel("%s\n- - - -" % resource)

      if (message_flag) and (info['info'] != None):
        self._update_messages("%s: %s" % (resource, info['info']), color)

      self.Layout()

    def _update_messages(self, message, color = 'black'):
      logger.info('Messagio all\'utente: "%s"' % message)
      self._stream.append((message, color))
      if (not self._stream_flag.isSet()):
        writer = Thread(target=self._writer)
        writer.start()
    
    def _writer(self):
      self._stream_flag.set()
      while (len(self._stream) > 0):
        (message, color) = self._stream.popleft()
        #date = getdate('ntp').strftime('%c')
        date = getdate('local').strftime('%c')
        start = self.messages_area.GetLastPosition()
        end = start + len(date) + 1
        if (start != 0):
          txt = ("\n%s %s" % (date, message))
        else:
          txt = ("%s %s" % (date, message))
        self.messages_area.AppendText(txt)
        self.messages_area.ScrollLines(-1)
        self.messages_area.SetStyle(start, end, wx.TextAttr(color))
      self._stream_flag.clear()

def getdate(type = 'local'):
  if type == 'ntp':
    date = datetime.fromtimestamp(timestampNtp())
  elif type == 'local':
    date = datetime.fromtimestamp(time.time())
  return date

def parse():
  '''
  Parsing dei parametri da linea di comando
  '''

  config = ConfigParser()

  if (path.exists(paths.CONF_MAIN)):
    config.read(paths.CONF_MAIN)
    logger.info('Caricata configurazione da %s' % paths.CONF_MAIN)

  parser = OptionParser(version = __version__, description = '')

  # Task options
  # --------------------------------------------------------------------------
  section = 'task'
  if (not config.has_section(section)):
    config.add_section(section)

  option = 'tasktimeout'
  value = '3600'
  try:
    value = config.getint(section, option)
  except (ValueError, NoOptionError):
    config.set(section, option, value)
  parser.add_option('--task-timeout', dest = option, type = 'int', default = value,
                    help = 'global timeout (in seconds) for each task [%s]' % value)

  option = 'testtimeout'
  value = '60'
  try:
    value = config.getint(section, option)
  except (ValueError, NoOptionError):
    config.set(section, option, value)
  parser.add_option('--test-timeout', dest = option, type = 'float', default = value,
                    help = 'timeout (in seconds as float number) for each test in a task [%s]' % value)

  option = 'scheduler'
  value = 'https://finaluser.agcom244.fub.it/Scheduler'
  try:
    value = config.get(section, option)
  except (ValueError, NoOptionError):
    config.set(section, option, value)
  parser.add_option('-s', '--scheduler', dest = option, default = value,
                    help = 'complete url for schedule download [%s]' % value)

  option = 'httptimeout'
  value = '60'
  try:
    value = config.getint(section, option)
  except (ValueError, NoOptionError):
    config.set(section, option, value)
  parser.add_option('--http-timeout', dest = option, type = 'int', default = value,
                    help = 'timeout (in seconds) for http operations [%s]' % value)

  # Client options
  # --------------------------------------------------------------------------
  section = 'client'
  if (not config.has_section(section)):
    config.add_section(section)

  option = 'clientid'
  value = None
  try:
    value = config.get(section, option)
  except (ValueError, NoOptionError):
    pass
  parser.add_option('-c', '--clientid', dest = option, default = value,
                    help = 'client identification string [%s]' % value)

  option = 'username'
  value = 'anonymous'
  try:
    value = config.get(section, option)
  except (ValueError, NoOptionError):
    config.set(section, option, value)
  parser.add_option('--username', dest = option, default = value,
                    help = 'username for FTP login [%s]' % value)

  option = 'password'
  value = '@anonymous'
  try:
    value = config.get(section, option)
  except (ValueError, NoOptionError):
    config.set(section, option, value)
  parser.add_option('--password', dest = option, default = value,
                    help = 'password for FTP login [%s]' % value)

  # Profile options
  # --------------------------------------------------------------------------
  section = 'profile'
  if (not config.has_section(section)):
    config.add_section(section)

  option = 'bandwidthup'
  value = 64
  try:
    value = config.getint(section, option)
  except (ValueError, NoOptionError):
    pass
  parser.add_option('--up', dest = option, default = value, type = 'int',
                    help = 'upload bandwidth [%s]' % value)

  option = 'bandwidthdown'
  value = 1000
  try:
    value = config.getint(section, option)
  except (ValueError, NoOptionError):
    pass
  parser.add_option('--down', dest = option, default = value, type = 'int',
                    help = 'download bandwidth [%s]' % value)

  with open(paths.CONF_MAIN, 'w') as file:
    config.write(file)

  (options, args) = parser.parse_args()

  # Verifica che le opzioni obbligatorie siano presenti
  # --------------------------------------------------------------------------

  try:

    parser.check_required('--clientid')
    config.set('client', 'clientid', options.clientid)

    parser.check_required('--up')
    config.set('profile', 'bandwidthup', options.bandwidthup)

    parser.check_required('--down')
    config.set('profile', 'bandwidthdown', options.bandwidthdown)

  finally:
    with open(paths.CONF_MAIN, 'w') as file:
      config.write(file)

  with open(paths.CONF_MAIN, 'r') as file:
    md5 = hashlib.md5(file.read()).hexdigest()

  return (options, args, md5)

def getclient(options):

  profile = Profile(id = None, upload = options.bandwidthup,
                    download = options.bandwidthdown)
  isp = Isp('fub001')
  return Client(id = options.clientid, profile = profile, isp = isp,
                geocode = None, username = 'speedtest',
                password = options.password)

if __name__ == "__main__":

  logger.info('Starting Ne.Me.Sys. Speedtest v.%s' % __version__)

  app = wx.PySimpleApp(0)
  if (platform.startswith('win')):
    wx.CallLater(200, sleeper)
  wx.InitAllImageHandlers()
  frame_1 = Frame(None, -1, "", style = wx.DEFAULT_FRAME_STYLE & ~(wx.RESIZE_BORDER | wx.RESIZE_BOX))
  app.SetTopWindow(frame_1)
  frame_1.Show()
  app.MainLoop()

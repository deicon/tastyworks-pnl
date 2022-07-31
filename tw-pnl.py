#!/usr/bin/python3
#
# Copyright (C) 2020-2021 Florian La Roche <Florian.LaRoche@gmail.com>
# https://github.com/laroche/tastyworks-pnl
#
# Generate data for a German tax income statement from Tastyworks trade history.
#
#
# Download your trade history as csv file from
# https://trade.tastyworks.com/index.html#/transactionHistoryPage
# (Choose 'Activity' and then 'History' and then setup the filter for a
# custom period of time and download it as csv file.)
# Newest entries in the csv file should be on the top and it should contain the complete
# history over all years. The csv file has the following first line:
# Date/Time,Transaction Code,Transaction Subcode,Symbol,Buy/Sell,Open/Close,\
#   Quantity,Expiration Date,Strike,Call/Put,Price,Fees,Amount,Description,Account Reference
#
#
# Install on Debian/Ubuntu-based systems:
#
# sudo apt-get install python3-pandas
#
#
# pylint: disable=C0103,C0111,C0114,C0116,C0326,C0330
#

import enum
import sys
import os
import getopt
from collections import deque
import math
import datetime as pydatetime
import pandas

# Damit werden auch Verluste beim Schreiben von Optionen berechnet. Dies ist
# nicht nach aktuellem Steuergesetz der Fall.
bmf_force = False

tax_output = None
#tax_output = '2020'

convert_currency = True

# For an unknown symbol (underlying), assume it is a individual/normal stock.
# Otherwise you need to adjust the hardcoded list in this script.
assume_stock = False

eurusd = None

# Setup 'eurusd' as dict() to contain the EURUSD exchange rate on a given date
# based on official data from bundesbank.de.
# If the file 'eurusd.csv' does not exist, download the data from
# the bundesbank directly.
def read_eurusd():
    global eurusd
    url = 'eurusd.csv'
    if not os.path.exists(url):
        url = 'https://www.bundesbank.de/statistic-rmi/StatisticDownload?tsId=BBEX3.D.USD.EUR.BB.AC.000&its_csvFormat=en&its_fileFormat=csv&mode=its&its_from=2010'
    eurusd = pandas.read_csv(url, skiprows=5, skipfooter=2, names=['date', 'eurusd', 'nix'],
        usecols=['date', 'eurusd'], na_values=['.'], engine='python')
    eurusd = dict(eurusd.values.tolist())

def isnan(x):
    return str(x) == 'nan'

def get_eurusd(date, debug=False):
    while True:
        try:
            x = eurusd[date]
        except KeyError:
            print('ERROR: No EURUSD conversion data available for %s,'
                ' please download newer data into the file eurusd.csv.' % date)
            sys.exit(1)
        if not isnan(x):
            return x
        if debug:
            print('EURUSD conversion not found for', date)
        date = str(pydatetime.date(*map(int, date.split('-'))) - pydatetime.timedelta(days=1))

def eur2usd(x, date, conv=None):
    if convert_currency:
        if conv is None:
            return x * get_eurusd(date)
        return x * conv
    return x

def usd2eur(x, date, conv=None):
    if convert_currency:
        if conv is None:
            return x / get_eurusd(date)
        return x / conv
    return x

def check_tcode(tcode, tsubcode, description):
    if tcode not in ('Money Movement', 'Trade', 'Receive Deliver'):
        raise
    if tcode == 'Money Movement':
        if tsubcode not in ('Transfer', 'Deposit', 'Credit Interest', 'Balance Adjustment',
            'Fee', 'Withdrawal', 'Dividend', 'Debit Interest', 'Mark to Market'):
            raise
        if tsubcode == 'Balance Adjustment' and description != 'Regulatory fee adjustment':
            raise
    elif tcode == 'Trade':
        if tsubcode not in ('Sell to Open', 'Buy to Close', 'Buy to Open', 'Sell to Close', 'Buy', 'Sell'):
            raise
    elif tcode == 'Receive Deliver':
        if tsubcode not in ('Sell to Open', 'Buy to Close', 'Buy to Open', 'Sell to Close',
            'Expiration', 'Assignment', 'Exercise', 'Forward Split', 'Reverse Split',
            'Special Dividend', 'Cash Settled Exercise'): # XXX, 'Cash Settled Assignment'):
            raise
        if tsubcode == 'Assignment' and description != 'Removal of option due to assignment':
            raise
        if tsubcode == 'Exercise' and description != 'Removal of option due to exercise':
            raise

def check_param(buysell, openclose, callput):
    if str(buysell) not in ('nan', 'Buy', 'Sell'):
        raise
    if str(openclose) not in ('nan', 'Open', 'Close'):
        raise
    if str(callput) not in ('nan', 'C', 'P'):
        raise

def check_trade(tsubcode, check_amount, amount, asset_type):
    #print('FEHLER:', check_amount, amount)
    if tsubcode in ('Buy', 'Sell', 'Cash Settled Exercise', 'Special Dividend'):
        pass
    elif tsubcode not in ('Expiration', 'Assignment', 'Exercise'):
        if asset_type == AssetType.Crypto:
            if not math.isclose(check_amount, amount, abs_tol=0.01):
                raise
        else:
            if not math.isclose(check_amount, amount, abs_tol=0.001):
                raise
    else:
        if not isnan(amount) and amount != .0:
            raise
        if not isnan(check_amount) and check_amount != .0:
            raise

class AssetType(enum.IntEnum):
    LongOption = 1
    ShortOption = 2
    IndStock = 3
    AktienFond = 4
    MischFond = 5
    ImmobilienFond = 6
    OtherStock = 7
    Crypto = 8
    Future = 9
    Transfer = 10
    Dividend = 11
    Interest = 12
    WithholdingTax = 13
    OrderPayments = 14
    Fee = 15

def transaction_type(asset_type):
    t = ['', 'Long-Option', 'Stillhalter-Option', 'Aktie', 'Aktienfond', 'Mischfond', 'Immobilienfond',
        'Sonstiges', 'Krypto', 'Future', 'Ein/Auszahlung', 'Dividende', 'Zinsen',
        'Quellensteuer', 'Ordergebühr', 'Brokergebühr']
    if int(asset_type) >= 1 and int(asset_type) <= 15:
        return t[asset_type]
    return ''

# https://en.wikipedia.org/wiki/List_of_S%26P_500_companies
SP500 = ('A', 'AAL', 'AAP', 'AAPL', 'ABBV', 'ABC', 'ABMD', 'ABT', 'ACN', 'ADBE',
    'ADI', 'ADM', 'ADP', 'ADSK', 'AEE', 'AEP', 'AES', 'AFL', 'AIG', 'AIZ',
    'AJG', 'AKAM', 'ALB', 'ALGN', 'ALK', 'ALL', 'ALLE', 'AMAT', 'AMCR', 'AMD',
    'AME', 'AMGN', 'AMP', 'AMT', 'AMZN', 'ANET', 'ANSS', 'ANTM', 'AON', 'AOS',
    'APA', 'APD', 'APH', 'APTV', 'ARE', 'ATO', 'ATVI', 'AVB', 'AVGO', 'AVY',
    'AWK', 'AXP', 'AZO', 'BA', 'BAC', 'BAX', 'BBWI', 'BBY', 'BDX', 'BEN',
    'BF.B', 'BIIB', 'BIO', 'BK', 'BKNG', 'BKR', 'BLK', 'BLL', 'BMY', 'BR',
    'BRK.B', 'BRO', 'BSX', 'BWA', 'BXP', 'C', 'CAG', 'CAH', 'CARR', 'CAT',
    'CB', 'CBOE', 'CBRE', 'CCI', 'CCL', 'CDAY', 'CDNS', 'CDW', 'CE', 'CERN',
    'CF', 'CFG', 'CHD', 'CHRW', 'CHTR', 'CI', 'CINF', 'CL', 'CLX', 'CMA',
    'CMCSA', 'CME', 'CMG', 'CMI', 'CMS', 'CNC', 'CNP', 'COF', 'COO', 'COP',
    'COST', 'CPB', 'CPRT', 'CRL', 'CRM', 'CSCO', 'CSX', 'CTAS', 'CTLT', 'CTRA',
    'CTSH', 'CTVA', 'CTXS', 'CVS', 'CVX', 'CZR', 'D', 'DAL', 'DD', 'DE', 'DFS',
    'DG', 'DGX', 'DHI', 'DHR', 'DIS', 'DISCA', 'DISCK', 'DISH', 'DLR', 'DLTR',
    'DOV', 'DOW', 'DPZ', 'DRE', 'DRI', 'DTE', 'DUK', 'DVA', 'DVN', 'DXC',
    'DXCM', 'EA', 'EBAY', 'ECL', 'ED', 'EFX', 'EIX', 'EL', 'EMN', 'EMR',
    'ENPH', 'EOG', 'EPAM', 'EQIX', 'EQR', 'ES', 'ESS', 'ETN', 'ETR', 'ETSY',
    'EVRG', 'EW', 'EXC', 'EXPD', 'EXPE', 'EXR', 'F', 'FANG', 'FAST', 'FB',
    'FBHS', 'FCX', 'FDS', 'FDX', 'FE', 'FFIV', 'FIS', 'FISV', 'FITB', 'FLT',
    'FMC', 'FOX', 'FOXA', 'FRC', 'FRT', 'FTNT', 'FTV', 'GD', 'GE', 'GILD',
    'GIS', 'GL', 'GLW', 'GM', 'GNRC', 'GOOG', 'GOOGL', 'GPC', 'GPN', 'GPS',
    'GRMN', 'GS', 'GWW', 'HAL', 'HAS', 'HBAN', 'HCA', 'HD', 'HES', 'HIG',
    'HII', 'HLT', 'HOLX', 'HON', 'HPE', 'HPQ', 'HRL', 'HSIC', 'HST', 'HSY',
    'HUM', 'HWM', 'IBM', 'ICE', 'IDXX', 'IEX', 'IFF', 'ILMN', 'INCY', 'INFO',
    'INTC', 'INTU', 'IP', 'IPG', 'IPGP', 'IQV', 'IR', 'IRM', 'ISRG', 'IT',
    'ITW', 'IVZ', 'J', 'JBHT', 'JCI', 'JKHY', 'JNJ', 'JNPR', 'JPM', 'K', 'KEY',
    'KEYS', 'KHC', 'KIM', 'KLAC', 'KMB', 'KMI', 'KMX', 'KO', 'KR', 'L', 'LDOS',
    'LEN', 'LH', 'LHX', 'LIN', 'LKQ', 'LLY', 'LMT', 'LNC', 'LNT', 'LOW',
    'LRCX', 'LUMN', 'LUV', 'LVS', 'LW', 'LYB', 'LYV', 'MA', 'MAA', 'MAR',
    'MAS', 'MCD', 'MCHP', 'MCK', 'MCO', 'MDLZ', 'MDT', 'MET', 'MGM', 'MHK',
    'MKC', 'MKTX', 'MLM', 'MMC', 'MMM', 'MNST', 'MO', 'MOS', 'MPC', 'MPWR',
    'MRK', 'MRNA', 'MRO', 'MS', 'MSCI', 'MSFT', 'MSI', 'MTB', 'MTCH', 'MTD',
    'MU', 'NCLH', 'NDAQ', 'NEE', 'NEM', 'NFLX', 'NI', 'NKE', 'NLOK', 'NLSN',
    'NOC', 'NOW', 'NRG', 'NSC', 'NTAP', 'NTRS', 'NUE', 'NVDA', 'NVR', 'NWL',
    'NWS', 'NWSA', 'NXPI', 'O', 'ODFL', 'OGN', 'OKE', 'OMC', 'ORCL', 'ORLY',
    'OTIS', 'OXY', 'PAYC', 'PAYX', 'PBCT', 'PCAR', 'PEAK', 'PEG', 'PENN',
    'PEP', 'PFE', 'PFG', 'PG', 'PGR', 'PH', 'PHM', 'PKG', 'PKI', 'PLD', 'PM',
    'PNC', 'PNR', 'PNW', 'POOL', 'PPG', 'PPL', 'PRU', 'PSA', 'PSX', 'PTC',
    'PVH', 'PWR', 'PXD', 'PYPL', 'QCOM', 'QRVO', 'RCL', 'RE', 'REG', 'REGN',
    'RF', 'RHI', 'RJF', 'RL', 'RMD', 'ROK', 'ROL', 'ROP', 'ROST', 'RSG', 'RTX',
    'SBAC', 'SBNY', 'SBUX', 'SCHW', 'SEDG', 'SEE', 'SHW', 'SIVB', 'SJM', 'SLB',
    'SNA', 'SNPS', 'SO', 'SPG', 'SPGI', 'SRE', 'STE', 'STT', 'STX', 'STZ',
    'SWK', 'SWKS', 'SYF', 'SYK', 'SYY', 'T', 'TAP', 'TDG', 'TDY', 'TECH',
    'TEL', 'TER', 'TFC', 'TFX', 'TGT', 'TJX', 'TMO', 'TMUS', 'TPR', 'TRMB',
    'TROW', 'TRV', 'TSCO', 'TSLA', 'TSN', 'TT', 'TTWO', 'TWTR', 'TXN', 'TXT',
    'TYL', 'UA', 'UAA', 'UAL', 'UDR', 'UHS', 'ULTA', 'UNH', 'UNP', 'UPS',
    'URI', 'USB', 'V', 'VFC', 'VIAC', 'VLO', 'VMC', 'VNO', 'VRSK', 'VRSN',
    'VRTX', 'VTR', 'VTRS', 'VZ', 'WAB', 'WAT', 'WBA', 'WDC', 'WEC', 'WELL',
    'WFC', 'WHR', 'WLTW', 'WM', 'WMB', 'WMT', 'WRB', 'WRK', 'WST', 'WY',
    'WYNN', 'XEL', 'XLNX', 'XOM', 'XRAY', 'XYL', 'YUM', 'ZBH', 'ZBRA', 'ZION',
    'ZTS')

# https://en.wikipedia.org/wiki/NASDAQ-100
NASDAQ100 = ('ATVI', 'ADBE', 'AMD', 'ABNB', 'ALGN', 'GOOGL', 'GOOG', 'AMZN', 'AEP',
    'AMGN', 'ADI', 'ANSS', 'AAPL', 'AMAT', 'ASML', 'TEAM', 'ADSK', 'ADP',
    'BIDU', 'BIIB', 'BKNG', 'AVGO', 'CDNS', 'CHTR', 'CTAS', 'CSCO', 'CTSH',
    'CMCSA', 'CPRT', 'COST', 'CRWD', 'CSX', 'DDOG', 'DXCM', 'DOCU', 'DLTR',
    'EBAY', 'EA', 'EXC', 'FAST', 'FISV', 'FTNT', 'GILD', 'HON', 'IDXX', 'ILMN',
    'INTC', 'INTU', 'ISRG', 'JD', 'KDP', 'KLAC', 'KHC', 'LRCX', 'LCID', 'LULU',
    'MAR', 'MRVL', 'MTCH', 'MELI', 'FB', 'MCHP', 'MU', 'MSFT', 'MRNA', 'MDLZ',
    'MNST', 'NTES', 'NFLX', 'NVDA', 'NXPI', 'ORLY', 'OKTA', 'PCAR', 'PANW',
    'PAYX', 'PYPL', 'PTON', 'PEP', 'PDD', 'QCOM', 'REGN', 'ROST', 'SGEN',
    'SIRI', 'SWKS', 'SPLK', 'SBUX', 'SNPS', 'TMUS', 'TSLA', 'TXN', 'VRSN',
    'VRSK', 'VRTX', 'WBA', 'WDAY', 'XEL', 'XLNX', 'ZM', 'ZS')

def read_sp500():
    table = pandas.read_html('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
    df = table[0]
    df.drop('SEC filings', axis=1, inplace=True)
    return df

def print_sp500():
    import pprint
    df = read_sp500()
    #df['Symbol'] = df['Symbol'].str.replace('.', '/')
    symbols = df['Symbol'].values.tolist()
    symbols.sort()
    p = pprint.pformat(symbols, width=79, compact=True, indent=4)
    print(p)

def read_nasdaq100():
    table = pandas.read_html('https://en.wikipedia.org/wiki/NASDAQ-100')
    df = table[3]
    return df

def print_nasdaq100():
    import pprint
    df = read_nasdaq100()
    #df['Ticker'] = df['Ticker'].str.replace('.', '/')
    symbols = df['Ticker'].values.tolist()
    p = pprint.pformat(symbols, width=79, compact=True, indent=4)
    print(p)


# Is the symbol a individual stock or anything else
# like an ETF or fond?
def is_stock(symbol, tsubcode):
    # Crypto assets like BTC/USD or ETH/USD:
    if symbol[-4:] == '/USD':
        return AssetType.Crypto
    #if symbol in ('SPY','IWM','QQQ'):
    #    return AssetType.AktienFond
    # Well known ETFs:
    if symbol in ('DIA','DXJ','EEM','EFA','EFA','EWW','EWZ','FEZ','FXB','FXE','FXI',
        'GDX','GDXJ','IWM','IYR','KRE','OIH','QQQ','TQQQ',
        'RSX','SMH','SPY','NOBL','UNG','XBI','XHB','XLB',
        'XLE','XLF','XLI','XLK','XLP','XLU','XLV','XME','XOP','XRT','XLRE'):
        return AssetType.OtherStock # AktienFond
    # Just an example, unfortunately EQQQ cannot be traded with Tastyworks:
    if symbol in ('EQQQ',):
        return AssetType.AktienFond
    if symbol in ('TLT','HYG','IEF','GLD','SLV','VXX','UNG','USO'):
        return AssetType.OtherStock
    # Well known individual stock names:
    if symbol in SP500 or symbol in NASDAQ100:
        return AssetType.IndStock
    if symbol.startswith('/'):
        if tsubcode not in ('Buy', 'Sell'):
            raise
        return AssetType.Future
    # The conservative way is to through an exception if we are not sure.
    if not assume_stock:
        print('No idea if this is a stock:', symbol)
        print('Use the option --assume-individual-stock to assume ' +
            'individual stock for all unknown symbols.')
        raise
    # Just assume this is a normal stock if not in the above list
    return AssetType.IndStock

def sign(x):
    if x >= 0:
        return 1
    return -1

# return date of one year earlier:
def prev_year(date):
    if date is None:
        return None
    return str(int(date[:4]) - 1) + date[4:]

# 'fifos' is a dictionary with 'asset' names. It contains a FIFO
# 'deque()' with a list of 'price' (as float), 'price_usd' (as float),
# 'quantity' (as integer), 'date' of purchase and 'tax_free'.
def fifo_add(fifos, quantity, price, price_usd, asset, is_option, date=None,
    tax_free=False, debug=False, debugfifo=False, debugcurr=False):
    prevyear = prev_year(date)
    (pnl, pnl_notax, term_losses) = (.0, .0, .0)
    if quantity == 0:
        return (pnl, pnl_notax, term_losses)
    if debug:
        print_fifos(fifos)
        print('fifo_add', quantity, price, asset)
    # Find the right FIFO queue for our asset:
    if fifos.get(asset) is None:
        fifos[asset] = deque()
    fifo = fifos[asset]
    # If the queue is empty, just add it to the queue:
    while len(fifo) > 0:
        # If we add assets into the same trading direction,
        # just add the asset into the queue. (Buy more if we are
        # already long, or sell more if we are already short.)
        if sign(fifo[0][2]) == sign(quantity):
            break
        # Here we start removing entries from the FIFO.
        # Check if the FIFO queue has enough entries for
        # us to finish:
        if abs(fifo[0][2]) >= abs(quantity):
            if is_option and quantity > 0:
                pnl -= quantity * price
                if bmf_force and price > fifo[0][0]:
                    term_losses += quantity * (price - fifo[0][0])
                    #print('PLANA', term_losses)
                    if term_losses < .0:
                        raise
            else:
                p = quantity * (price - fifo[0][0])
                if date is None or \
                    (fifo[0][3] > prevyear and quantity < 0 and \
                    not fifo[0][4] and not tax_free):
                    pnl -= p
                else:
                    pnl_notax -= p
                    if date is not None and debugcurr:
                        print(fifo[0][3], '%.2f' % (-p / 10000.0),
                            'over one year ago or paying back loan or tax free')
                if is_option and quantity < 0 and p > .0:
                    #print('Termingeschäft-Verlust von %.2f:' % p)
                    term_losses += p
            if debugfifo:
                print('DEBUG FIFO: %s: del %7d * %8.4f (new: %8.4f) = %8.2f pnl %8.2f notax' \
                    % (asset, quantity, fifo[0][0], price, pnl, pnl_notax))
            fifo[0][2] += quantity
            if fifo[0][2] == 0:
                fifo.popleft()
                if len(fifo) == 0:
                    del fifos[asset]
            return (pnl, pnl_notax, term_losses)
        # Remove the oldest FIFO entry and continue
        # the loop for further entries (or add the
        # remaining entries into the FIFO).
        if is_option and quantity > 0:
            pnl += fifo[0][2] * price
            if bmf_force and price > fifo[0][0]:
                term_losses -= fifo[0][2] * (price - fifo[0][0])
                #print('PLANB', term_losses)
                if term_losses < .0:
                    raise
        else:
            p = fifo[0][2] * (price - fifo[0][0])
            if date is None or \
                (fifo[0][3] > prevyear and quantity < 0 and \
                not fifo[0][4] and not tax_free):
                pnl += p
            else:
                pnl_notax += p
                if date is not None and debugcurr:
                    print(fifo[0][3], '%.2f' % (p / 10000.0),
                        'over one year ago or paying back loan or tax free')
            if is_option and quantity < 0 and p < .0:
                #print('Termingeschäft-Verlust von %.2f:' % -p)
                term_losses -= p
        if debugfifo:
            print('DEBUG FIFO: %s: del %7d * %8.4f (new: %8.4f) = %8.2f pnl, %8.2f notax' \
                % (asset, -fifo[0][2], fifo[0][0], price, pnl, pnl_notax))
        quantity += fifo[0][2]
        fifo.popleft()
    # Just add this to the FIFO queue:
    fifo.append([price, price_usd, quantity, date, tax_free])
    # selling an option is taxed directly as income
    if is_option and quantity < 0:
        pnl -= quantity * price
    if debugfifo:
        print('DEBUG FIFO: %s: add %7d * %8.2f = %8.2f pnl %8.2f notax' \
            % (asset, quantity, price, pnl, pnl_notax))
    return (pnl, pnl_notax, term_losses)

# Check if the first entry in the FIFO
# is 'long' the underlying or 'short'.
def fifos_islong(fifos, asset):
    return fifos[asset][0][2] > 0

def fifos_sum_usd(fifos):
    sum_usd = .0
    for fifo in fifos:
        if fifo != 'account-usd':
            for (price, price_usd, quantity, date, tax_free) in fifos[fifo]:
                sum_usd += price_usd * quantity
    return sum_usd

# stock (and option) split
def fifos_split(fifos, asset, ratio):
    for fifo in fifos:
        # adjust stock for split:
        if fifo == asset:
            for f in fifos[fifo]:
                f[0] = f[0] / ratio
                f[1] = f[1] / ratio
                f[2] = f[2] * ratio
        # XXX: implement option strike adjustment
        # fifo == asset + ' ' + 'P/C' + Strike + ' '

def print_fifos(fifos):
    print('open positions:')
    for fifo in fifos:
        print(fifo, fifos[fifo])

# account-usd should always be the same as total together with
# EURUSD conversion data. So just a sanity check:
def check_total(fifos, total):
    for (price, price_usd, quantity, date, tax_free) in fifos['account-usd']:
        total -= quantity / 10000
    if abs(total) > 0.004:
        print(total)
        raise

def show_plt(df):
    import matplotlib.pyplot as plt
    for i in ('account_total', 'net_total', 'pnl', 'usd_gains', 'term_loss'):
        df[i] = pandas.to_numeric(df[i]) # df[i].astype(float)
    #df.plot(x='datetime', y=['account_total', 'pnl', 'term_loss'])
    df2 = df.copy()
    df2['datetime2'] = pandas.to_datetime(df2['datetime'])
    monthly_totals = df2.resample(rule='M', on='datetime2').sum() # .mean() rule='Q' quarterly
    monthly_totals.plot(kind='bar', y=['pnl',])
    #monthly_list = pandas.date_range(start='2018-04-01', end='2021-07-31', freq='M')
    #print(monthly_list)
    df.plot(y=['net_total'])
    df.plot(y=['account_total'])
    df.plot(kind='bar', y=['pnl', 'usd_gains', 'term_loss'])
    df.plot(kind='bar', y=['usd_gains'])
    df.plot(kind='bar', y=['term_loss'])
    plt.show()

def print_yearly_summary(cur_year, curr_sym, dividends, withholding_tax,
        withdrawal, interest_recv, interest_paid, fee_adjustments, pnl_stocks_gains,
        pnl_stocks_losses, future, pnl, account_usd, account_usd_notax, total_fees,
        term_losses, total, fifos, verbose):
    print()
    print('Total sums paid and received in the year %s:' % cur_year)
    if dividends != .0 or withholding_tax != .0 or verbose:
        print('dividends received:      ', f'{dividends:10.2f}' + curr_sym)
        print('withholding tax paid:    ', f'{withholding_tax:10.2f}' + curr_sym)
    if withdrawal != .0:
        print('dividends paid:          ', f'{withdrawal:10.2f}' + curr_sym)
    print('interest received:       ', f'{interest_recv:10.2f}' + curr_sym)
    if interest_paid != .0:
        print('interest paid:           ', f'{interest_paid:10.2f}' + curr_sym)
    print('fee adjustments:         ', f'{fee_adjustments:10.2f}' + curr_sym)
    if pnl_stocks_gains != .0 or pnl_stocks_losses != .0 or verbose:
        print('pnl stocks gains:        ', f'{pnl_stocks_gains:10.2f}' + curr_sym)
        print('pnl stocks losses:       ', f'{pnl_stocks_losses:10.2f}' + curr_sym)
    if future != .0 or verbose:
        print('pnl futures:             ', f'{future:10.2f}' + curr_sym)
    print('pnl other:               ', f'{pnl:10.2f}' + curr_sym)
    print('pnl total:               ', '%10.2f' % (dividends + \
        withdrawal + interest_recv + interest_paid + fee_adjustments + \
        pnl_stocks_gains + pnl_stocks_losses + future + pnl) + curr_sym)
    print('USD currency gains:      ', f'{account_usd:10.2f}' + curr_sym)
    print('USD curr. gains (no tax):', f'{account_usd_notax:10.2f}' + curr_sym)
    print('losses future contracts: ', f'{-term_losses:10.2f}' + curr_sym)
    print()
    print('New end sums and open positions:')
    print('total fees paid:         ', f'{total_fees:10.2f}' + curr_sym)
    print('account cash balance:    ', f'{total:10.2f}' + '$')
    print_fifos(fifos)
    print()

def get_summary(new_wk, year):
    stock_gains = .0
    stock_losses = .0
    fonds_gains = .0
    fonds_losses = .0
    dividend_gains = .0
    dividend_losses = .0
    withholdingtax = .0
    interest_gains = .0
    interest_losses = .0
    fee_adjustments = .0
    futures_gains = .0
    futures_losses = .0
    other_gains = .0
    other_losses = .0
    option_gains = .0
    option_losses = .0
    soption_gains = .0
    soption_losses = .0
    crypto_gains = .0
    crypto_losses = .0
    usd = .0
    usd_notax = .0
    for i in new_wk:
        type = i[1]
        usd += float(i[9])
        usd_notax += float(i[10])
        pnl = .0
        if i[2] != '':
            pnl = float(i[2])
        tax_free = i[8]
        # steuerfreie Zahlungen:
        if type in ('Brokergebühr', 'Ordergebühr', 'Zinsen', 'Dividende', 'Quellensteuer'):
            if tax_free == False:
                raise
        # keine steuerfreien Zahlungen:
        if type in ('Ein/Auszahlung', 'Aktie', 'Aktienfond', 'Mischfond',
            'Immobilienfond', 'Sonstiges', 'Long-Option', 'Future'):
            if tax_free == True:
                raise
        if type in ('Ein/Auszahlung', 'Brokergebühr'):
            pass
        elif type == 'Ordergebühr':
            fee_adjustments += pnl
        elif type == 'Aktie':
            if pnl < .0:
                stock_losses += pnl
            else:
                stock_gains += pnl
        elif type in ('Aktienfond', 'Mischfond', 'Immobilienfond'):
            if pnl < .0:
                fonds_losses += pnl
            else:
                fonds_gains += pnl
        elif type == 'Zinsen':
            if pnl < .0:
                interest_losses += pnl
            else:
                interest_gains += pnl
        elif type == 'Sonstiges':
            if pnl < .0:
                other_losses += pnl
            else:
                other_gains += pnl
        elif type == 'Long-Option':
            if pnl < .0:
                option_losses += pnl
            else:
                option_gains += pnl
        elif type == 'Stillhalter-Option':
            if pnl < .0:
                soption_losses += pnl
            else:
                soption_gains += pnl
            # Kontrolle:  Praemien sind alle steuerfrei, Glattstellungen nicht:
            if tax_free == False:
                if pnl > .0:
                    raise
            else:
                if pnl < .0:
                    raise
        elif type == 'Krypto':
            if pnl < .0:
                crypto_losses += pnl
            else:
                crypto_gains += pnl
        elif type == 'Dividende':
            if pnl < .0:
                dividend_losses += pnl
            else:
                dividend_gains += pnl
        elif type == 'Quellensteuer':
            withholdingtax += pnl
        elif type == 'Future':
            if pnl < .0:
                future_losses += pnl
            else:
                future_gains += pnl
        else:
            print(i)
            raise

    # header:
    new_wk.insert(0, ['Tastyworks %s' % year, '', '', '', '', '', '', '', '', '', ''])
    new_wk.insert(1, ['', '', '', '', '', '', '', '', '', '', ''])
    new_wk.insert(1, ['', '', '', '', '', '', '', '', '', '', ''])
    new_wk.insert(1, ['', '', '', '', '', '', '', '', '', '', ''])

    # summary at the end:
    new_wk.append(['', '', '', '', '', '', '', '', '', '', ''])
    new_wk.append(['', '', '', '', '', '', '', '', '', '', ''])
    new_wk.append(['', '', '', '', '', '', '', '', '', '', ''])
    new_wk.append(['', '', '', '', '', '', '', '', '', '', ''])
    if fonds_gains != .0 or fonds_losses != .0:
        new_wk.append(['Investmentfonds:', '', '', '', f'{fonds_gains + fonds_losses:.2f}', 'Euro', '', '', '', '', ''])
    if stock_gains != .0 or stock_losses != .0:
        new_wk.append(['Aktien Gewinne:', '', '', '', f'{stock_gains:.2f}', 'Euro', '', '', '', '', ''])
        new_wk.append(['Aktien Verluste:', '', '', '', f'{stock_losses:.2f}', 'Euro', '', '', '', '', ''])
        new_wk.append(['Aktien Gesamt:', '', '', '', f'{stock_gains + stock_losses:.2f}', 'Euro', '', '', '', '', ''])
    if dividend_gains != .0 or dividend_losses != .0:
        new_wk.append(['Dividenden:', '', '', '', f'{dividend_gains:.2f}', 'Euro', '', '', '', '', ''])
    if dividend_losses != .0:
        new_wk.append(['bezahlte Dividenden:', '', '', '', f'{dividend_losses:.2f}', 'Euro', '', '', '', '', ''])
        new_wk.append(['Dividenden Gesamt:', '', '', '', f'{dividend_gains + dividend_losses:.2f}', 'Euro', '', '', '', '', ''])
    if withholdingtax != .0:
        new_wk.append(['Quellensteuer:', '', '', '', f'{withholdingtax:.2f}', 'Euro', '', '', '', '', ''])
    if other_gains != .0 or other_losses != .0:
        new_wk.append(['Sonstige Gewinne:', '', '', '', f'{other_gains:.2f}', 'Euro', '', '', '', '', ''])
        new_wk.append(['Sonstige Verluste:', '', '', '', f'{other_losses:.2f}', 'Euro', '', '', '', '', ''])
        new_wk.append(['Sonstige Gesamt:', '', '', '', f'{other_gains + other_losses:.2f}', 'Euro', '', '', '', '', ''])
    if soption_gains != .0 or soption_losses != .0:
        new_wk.append(['Stillhalter Gewinne:', '', '', '', f'{soption_gains:.2f}', 'Euro', '', '', '', '', ''])
        new_wk.append(['Stillhalter Verluste:', '', '', '', f'{soption_losses:.2f}', 'Euro', '', '', '', '', ''])
        new_wk.append(['Stillhalter Gesamt:', '', '', '', f'{soption_gains + soption_losses:.2f}', 'Euro', '', '', '', '', ''])
    if option_gains != .0 or option_losses != .0:
        new_wk.append(['Long-Optionen Gewinne:', '', '', '', f'{option_gains:.2f}', 'Euro', '', '', '', '', ''])
        new_wk.append(['Long-Optionen Verluste:', '', '', '', f'{option_losses:.2f}', 'Euro', '', '', '', '', ''])
        new_wk.append(['Long-Optionen Gesamt:', '', '', '', f'{option_gains + option_losses:.2f}', 'Euro', '', '', '', '', ''])
    if crypto_gains != .0 or crypto_losses != .0:
        new_wk.append(['Krypto Gewinne:', '', '', '', f'{crypto_gains:.2f}', 'Euro', '', '', '', '', ''])
        new_wk.append(['Krypto Verluste:', '', '', '', f'{crypto_losses:.2f}', 'Euro', '', '', '', '', ''])
        new_wk.append(['Krypto Gesamt:', '', '', '', f'{crypto_gains + crypto_losses:.2f}', 'Euro', '', '', '', '', ''])
    if futures_gains != .0 or futures_losses != .0:
        new_wk.append(['Future Gewinne:', '', '', '', f'{futures_gains:.2f}', 'Euro', '', '', '', '', ''])
        new_wk.append(['Future Verluste:', '', '', '', f'{futures_losses:.2f}', 'Euro', '', '', '', '', ''])
        new_wk.append(['Future Gesamt:', '', '', '', f'{futures_gains + futures_losses:.2f}', 'Euro', '', '', '', '', ''])
    if interest_gains != .0 or interest_losses != .0:
        new_wk.append(['Zinseinnahmen:', '', '', '', f'{interest_gains:.2f}', 'Euro', '', '', '', '', ''])
        new_wk.append(['Zinsausgaben:', '', '', '', f'{interest_losses:.2f}', 'Euro', '', '', '', '', ''])
        new_wk.append(['Zinsen Gesamt:', '', '', '', f'{interest_gains + interest_losses:.2f}', 'Euro', '', '', '', '', ''])
    if fee_adjustments != .0:
        new_wk.append(['Ordergebühren:', '', '', '', f'{fee_adjustments:.2f}', 'Euro', '', '', '', '', ''])
    new_wk.append(['', '', '', '', '', '', '', '', '', '', ''])
    total_other = dividend_gains + dividend_losses + other_gains + other_losses + soption_gains + soption_losses \
        + option_gains + option_losses + futures_gains + futures_losses + interest_gains + interest_losses + fee_adjustments
    total = total_other + fonds_gains + fonds_losses + stock_gains + stock_losses + crypto_losses + crypto_gains
    new_wk.append(['Alle Sonstige Gesamt:', '', '', '', f'{total_other:.2f}', 'Euro', '', '', '', '', ''])
    new_wk.append(['Gesamt:', '', '', '', f'{total:.2f}', 'Euro', '', '', '', '', ''])
    new_wk.append(['', '', '', '', '', '', '', '', '', '', ''])
    new_wk.append(['Währungsgewinne USD:', '', '', '', f'{usd:.2f}', 'Euro', '', '', '', '', ''])
    new_wk.append(['Währungsgewinne USD (steuerfrei):', '', '', '', f'{usd_notax:.2f}', 'Euro', '', '', '', '', ''])

def check(wk, output_csv, output_excel, opt_long, verbose, show, debugfifo):
    #print(wk)
    splits = {}               # save data for stock/option splits
    curr_sym = '€'
    if not convert_currency:
        curr_sym = '$'
    fifos = {}
    total = .0                # account total
    (pnl_stocks_gains, pnl_stocks_losses, future, pnl) = (.0, .0, .0, .0)
    (account_usd, account_usd_notax) = (.0, .0)
    (dividends, withholding_tax, interest_recv, interest_paid) = (.0, .0, .0, .0)
    (withdrawal, fee_adjustments, total_fees, term_losses) = (.0, .0, .0, .0)
    cur_year = None
    prev_datetime = None
    check_account_ref = None
    new_wk = []
    for i in reversed(wk.index):
        # Date/Time,Transaction Code,Transaction Subcode,Symbol,Buy/Sell,Open/Close,\
        #   Quantity,Expiration Date,Strike,Call/Put,Price,Fees,Amount,Description,\
        #   Account Reference
        (datetime, tcode, tsubcode, symbol, buysell, openclose, quantity, expire, strike,
            callput, price, fees, amount, description, account_ref) = wk.iloc[i]
        if str(datetime)[16:] != ':00': # minimum output is minutes, seconds are 00 here
            raise
        datetime = str(datetime)[:16]
        if prev_datetime is not None and prev_datetime > datetime:
            raise
        prev_datetime = datetime
        date = datetime[:10] # year-month-day but no time
        if cur_year != datetime[:4]:
            if cur_year is not None:
                print_yearly_summary(cur_year, curr_sym, dividends, withholding_tax,
                    withdrawal, interest_recv, interest_paid, fee_adjustments,
                    pnl_stocks_gains, pnl_stocks_losses, future, pnl, account_usd, account_usd_notax,
                    total_fees, term_losses, total, fifos, verbose)
                (pnl_stocks_gains, pnl_stocks_losses, future, pnl) = (.0, .0, .0, .0)
                (account_usd, account_usd_notax) = (.0, .0)
                (dividends, withholding_tax, interest_recv, interest_paid) = (.0, .0, .0, .0)
                (withdrawal, fee_adjustments, total_fees, term_losses) = (.0, .0, .0, .0)
            cur_year = datetime[:4]
        check_tcode(tcode, tsubcode, description)
        check_param(buysell, openclose, callput)
        if check_account_ref is None:
            check_account_ref = account_ref
        if account_ref != check_account_ref: # check if this does not change over time
            raise
        (amount, fees) = (float(amount), float(fees))
        # option/stock splits are tax neutral, so zero out amount/fees for it:
        if tcode == 'Receive Deliver' and (tsubcode == 'Forward Split' or tsubcode == 'Reverse Split'):
            (amount, fees) = (.0, .0)
        conv_usd = get_eurusd(date)
        total_fees += usd2eur(fees, date, conv_usd)
        total += amount - fees
        eur_amount = usd2eur(amount - fees, date)
        # look at currency conversion gains:
        tax_free = False
        if tsubcode in ('Deposit', 'Credit Interest', 'Debit Interest', 'Dividend',
            'Fee', 'Balance Adjustment', 'Special Dividend'):
            tax_free = True
        if tsubcode == 'Withdrawal' and not isnan(symbol):
            tax_free = True
        # Stillhalterpraemien gelten als Zufluss und nicht als Anschaffung
        # und sind daher steuer-neutral:
        # XXX We use "Sell-to-Open" to find all "Stillhaltergeschäfte". This works
        # ok for me, but what happens if we have one long option and sell two? Will
        # Tastyworks split this into two transactions or keep this? With keeping this
        # as one transaction, we should split the currency gains transaction as well.
        # Could we detect this bad case within transactions?
        if tcode != 'Money Movement' and \
            not isnan(expire) and str(buysell) == 'Sell' and str(openclose) == 'Open':
            tax_free = True
        # USD as a big integer number:
        (usd_gains, usd_gains_notax, _) = fifo_add(fifos, int((amount - fees) * 10000),
            1 / conv_usd, 1, 'account-usd', False, date, tax_free, debugfifo=debugfifo)
        (usd_gains, usd_gains_notax) = (usd_gains / 10000.0, usd_gains_notax / 10000.0)
        account_usd += usd_gains
        account_usd_notax += usd_gains_notax

        asset = ''
        newdescription = ''

        if isnan(quantity):
            quantity = 1
        else:
            if tcode == 'Receive Deliver' and (tsubcode == 'Forward Split' or tsubcode == 'Reverse Split'):
                pass # splits might have further data, not quantity
            elif int(quantity) != quantity:
                # Hardcode AssetType.Crypto here again:
                if symbol[-4:] != '/USD':
                    raise
            else:
                quantity = int(quantity)

        if isnan(price):
            price = .0
        if price < .0:
            raise

        header = '%s %s' % (datetime, f'{eur_amount:10.2f}' + curr_sym)
        if verbose:
            header += ' %s' % f'{usd_gains:10.2f}' + '€'
        header += ' %s' % f'{amount - fees:10.2f}' + '$'
        #if verbose:
        #    header += ' %s' % f'{conv_usd:8.4f}'
        if tcode != 'Receive Deliver' or (tsubcode != 'Forward Split' and tsubcode != 'Reverse Split'):
            header += ' %5d' % quantity

        if tcode == 'Money Movement':
            local_pnl = '%.4f' % eur_amount
            term_loss = .0
            if tsubcode != 'Transfer' and fees != .0:
                raise
            if tsubcode == 'Transfer':
                local_pnl = ''
                asset = 'transfer'
                newdescription = description
                print(header, 'transferred:', description)
                asset_type = AssetType.Transfer
            elif tsubcode in ('Deposit', 'Credit Interest', 'Debit Interest'):
                if isnan(symbol):
                    asset = 'interest'
                    asset_type = AssetType.Interest
                    if amount > .0:
                        interest_recv += eur_amount
                    else:
                        interest_paid += eur_amount
                    if description != 'INTEREST ON CREDIT BALANCE':
                        newdescription = description
                        print(header, 'interest:', description)
                    else:
                        print(header, 'interest')
                else:
                    if amount > .0:
                        asset = 'dividends for %s' % symbol
                        asset_type = AssetType.Dividend
                        dividends += eur_amount
                        print(header, 'dividends: %s,' % symbol, description)
                    else:
                        asset = 'withholding tax for %s' % symbol
                        asset_type = AssetType.WithholdingTax
                        withholding_tax += eur_amount
                        print(header, 'withholding tax: %s,' % symbol, description)
                    newdescription = description
            elif tsubcode == 'Balance Adjustment':
                asset = 'balance adjustment'
                asset_type = AssetType.OrderPayments
                if opt_long:
                    print(header, 'balance adjustment')
                fee_adjustments += eur_amount
                total_fees += eur_amount
            elif tsubcode == 'Fee':
                if description == 'INTL WIRE FEE':
                    local_pnl = ''
                    asset = 'fee'
                    asset_type = AssetType.Fee
                    newdescription = description
                    print(header, 'fee:', description)
                    total_fees += eur_amount
                else:
                    # XXX In my case: stock borrow fee:
                    asset = 'stock borrow fees for %s' % symbol
                    asset_type = AssetType.Interest
                    newdescription = description
                    print(header, 'stock borrow fees: %s,' % symbol, description)
                    fee_adjustments += eur_amount
                    total_fees += eur_amount
                    if amount >= .0:
                        raise
            elif tsubcode == 'Withdrawal':
                if not isnan(symbol):
                    # XXX In my case: dividends paid for short stock:
                    asset = 'dividends paid for %s' % symbol
                    asset_type = AssetType.Dividend
                    newdescription = description
                    print(header, 'dividends paid: %s,' % symbol, description)
                    withdrawal += eur_amount
                    if amount >= .0:
                        raise
                else:
                    if description[:5] == 'FROM ':
                        asset = 'interest'
                        asset_type = AssetType.Interest
                        if amount > .0:
                            interest_recv += eur_amount
                        else:
                            interest_paid += eur_amount
                        if description != 'INTEREST ON CREDIT BALANCE':
                            newdescription = description
                            print(header, 'interest:', description)
                        else:
                            print(header, 'interest')
                    else:
                        # account deposit/withdrawal
                        local_pnl = ''
                        asset = 'transfer'
                        asset_type = AssetType.Transfer
                        newdescription = description
                        print(header, 'transferred:', description)
            elif tsubcode == 'Dividend':
                if amount > .0:
                    asset = 'dividends for %s' % symbol
                    asset_type = AssetType.Dividend
                    dividends += eur_amount
                    print(header, 'dividends: %s,' % symbol, description)
                else:
                    asset = 'withholding tax for %s' % symbol
                    asset_type = AssetType.WithholdingTax
                    withholding_tax += eur_amount
                    print(header, 'withholding tax: %s,' % symbol, description)
                newdescription = description
            elif tsubcode == 'Mark to Market':
                asset = 'mark-to-market for %s' % symbol
                asset_type = AssetType.Future
                future += eur_amount
                print(header, description)
                newdescription = description
        elif tcode == 'Receive Deliver' and (tsubcode == 'Forward Split' or tsubcode == 'Reverse Split'):
            # XXX: We might check that the two relevant entries have the same data for 'amount'.
            x = symbol + '-' + date
            # quantity for splits seems to be more like strike price and how it changes.
            # We use it to calculate the split ration / reverse ratio.
            if (tsubcode == 'Forward Split' and str(buysell) == 'Sell') or \
               (tsubcode == 'Reverse Split' and str(buysell) == 'Buy'):
                splits[x] = quantity
            else:
                oldquantity = splits[x]
                ratio = quantity / oldquantity
                if int(ratio) == ratio:
                    ratio = int(ratio)
                #print(symbol, quantity, oldquantity, ratio)
                fifos_split(fifos, symbol, ratio)
        else:
            asset = symbol
            if not isnan(expire):
                expire = pydatetime.datetime.strptime(expire, '%m/%d/%Y').strftime('%y-%m-%d')
                # XXX hack for future multiples
                # check https://tastyworks.freshdesk.com/support/solutions/articles/43000435192
                # SP500/Nasdaq/Russel2000 and corn:
                if asset[:3] in ('/ES', '/ZW', '/ZS', '/ZC'):
                    price *= 50.0
                elif asset[:3] in ('/NQ',):
                    price *= 20.0
                elif asset[:4] in ('/RTY',):
                    price *= 50.0
                # silver and gold:
                elif asset[:3] in ('/GC',):
                    price *= 100.0
                elif asset[:4] in ('/MGC',):
                    price *= 10.0
                elif asset[:3] in ('/SI',):
                    price *= 5000.0
                elif asset[:4] in ('/SIL',):
                    price *= 1000.0
                # oil and gas:
                elif asset[:3] in ('/CL',):
                    price *= 1000.0
                elif asset[:3] in ('/QM',):
                    price *= 500.0
                elif asset[:3] in ('/NG',):
                    price *= 10000.0
                # bitcoin:
                elif asset[:4] in ('/BTC',):
                    price *= 5.0
                elif asset[:4] in ('/MBT',):
                    price *= .1
                # interest rates:
                elif asset[:3] in ('/ZT',):
                    price *= 2000.0
                elif asset[:3] in ('/ZF', '/ZN', '/ZB', '/UB'):
                    price *= 1000.0
                else:
                    price *= 100.0
                if int(strike) == strike: # convert to integer for full numbers
                    strike = int(strike)
                asset = '%s %s%s %s' % (symbol, callput, strike, expire)
                asset_type = AssetType.LongOption
                if not isnan(expire) and ((str(buysell) == 'Sell' and str(openclose) == 'Open') or \
                    (str(buysell) == 'Buy' and str(openclose) == 'Close') or
                    (tsubcode in ('Expiration', 'Exercise', 'Assignment') and not fifos_islong(fifos, asset))):
                    asset_type = AssetType.ShortOption
            else:
                asset_type = is_stock(symbol, tsubcode)
            # 'buysell' is not set correctly for 'Expiration'/'Exercise'/'Assignment' entries,
            # so we look into existing positions to check if we are long or short (we cannot
            # be both, so this test should be safe):
            if str(buysell) == 'Sell' or \
                (tsubcode in ('Expiration', 'Exercise', 'Assignment') and fifos_islong(fifos, asset)):
                quantity = - quantity
            if tsubcode in ('Exercise', 'Assignment') and quantity < 0:
                print('Assignment/Exercise for a long option, please move pnl on next line to stock:')
            if tsubcode == 'Cash Settled Assignment':
                quantity = 1.0
            check_trade(tsubcode, - (quantity * price), amount, asset_type)
            price_usd = abs((amount - fees) / quantity)
            price = usd2eur(price_usd, date, conv_usd)
            (local_pnl, _, term_loss) = fifo_add(fifos, quantity, price, price_usd, asset,
                (asset_type == AssetType.LongOption) or (asset_type == AssetType.ShortOption),
                debugfifo=debugfifo)
            if term_loss < .0:
                raise
            term_losses += term_loss
            header = '%s %s' % (datetime, f'{local_pnl:10.2f}' + curr_sym)
            if verbose:
                header += ' %s' % f'{usd_gains:10.2f}' + '€'
            header += ' %s' % f'{amount-fees:10.2f}' + '$'
            #if verbose:
            #    header += ' %s' % f'{conv_usd:8.4f}'
            print(header, '%5d' % quantity, asset)
            if asset_type == AssetType.IndStock:
                if local_pnl > .0:
                    pnl_stocks_gains += local_pnl
                else:
                    pnl_stocks_losses += local_pnl
            elif asset_type == AssetType.Future:
                if tsubcode not in ('Buy', 'Sell'):
                    raise
                # XXX For futures we just add all payments as-is for taxes. We should add them
                # up until final closing instead. This should be changed. ???
                local_pnl = eur_amount
                future += local_pnl
            else:
                if cur_year >= '2018':
                    if asset_type == AssetType.AktienFond:
                        local_pnl *= 0.70
                    elif asset_type == AssetType.MischFond:
                        local_pnl *= 0.85
                    elif asset_type == AssetType.ImmobilienFond:
                        local_pnl *= 0.20
                pnl += local_pnl
            description = ''
            local_pnl = '%.4f' % local_pnl

        #check_total(fifos, total)

        net_total = total + fifos_sum_usd(fifos)

        if tax_output:
            if datetime[:4] == tax_output:
                if local_pnl != '':
                    local_pnl = '%.2f' % float(local_pnl)
                new_wk.append([datetime[:10], transaction_type(asset_type),
                        local_pnl, '%.2f' % eur_amount, '%.2f' % (amount - fees), '%.4f' % conv_usd,
                        quantity, asset,
                        tax_free, '%.2f' % usd_gains, '%.2f' % usd_gains_notax])
        else:
            new_wk.append([datetime, transaction_type(asset_type),
                local_pnl, '%.4f' % term_loss,
                '%.4f' % eur_amount, '%.4f' % amount, '%.4f' % fees, '%.4f' % conv_usd,
                quantity, asset, symbol, newdescription, '%.2f' % total, '%.2f' % net_total,
                tax_free, '%.4f' % usd_gains, '%.4f' % usd_gains_notax])

    wk.drop('Account Reference', axis=1, inplace=True)

    print_yearly_summary(cur_year, curr_sym, dividends, withholding_tax,
        withdrawal, interest_recv, interest_paid, fee_adjustments, pnl_stocks_gains,
        pnl_stocks_losses, future, pnl, account_usd, account_usd_notax, total_fees,
        term_losses, total, fifos, verbose)

    #print(wk)
    if tax_output:
        new_wk = sorted(new_wk, key=lambda x: x[1])
        get_summary(new_wk, tax_output)
        new_wk = pandas.DataFrame(new_wk, columns=('date', 'type', 'pnl',
            'eur_amount', 'usd_amount', 'eurusd', 'quantity', 'asset',
            'tax_free', 'usd_gains', 'usd_gains_notax'))
    else:
        new_wk = pandas.DataFrame(new_wk, columns=('datetime', 'type', 'pnl', 'term_loss',
            'eur_amount', 'usd_amount', 'fees', 'eurusd', 'quantity', 'asset', 'symbol',
            'description', 'account_total', 'net_total',
            'tax_free', 'usd_gains', 'usd_gains_notax'))
    if output_csv is not None:
        with open(output_csv, 'w') as f:
            new_wk.to_csv(f, index=False)
    if output_excel is not None:
        with pandas.ExcelWriter(output_excel) as f:
            new_wk.to_excel(f, index=False, sheet_name='Tastyworks Report') #, engine='xlsxwriter')
    #print(new_wk)

    if show:
        show_plt(new_wk)

def check_csv(csv_file):
    with open(csv_file) as f:
        content = f.readlines()
    if len(content) < 1 or content[0] != 'Date/Time,Transaction Code,' + \
        'Transaction Subcode,Symbol,Buy/Sell,Open/Close,Quantity,' + \
        'Expiration Date,Strike,Call/Put,Price,Fees,Amount,Description,' + \
        'Account Reference\n':
        print('ERROR: Wrong first line in csv file.')
        sys.exit(1)

def usage():
    print('tw-pnl.py [--assume-individual-stock][--long][--usd]' + \
        '[--output-csv=test.csv][--output-excel=test.xlsx][--help]' + \
        '[--verbose] *.csv')

def main(argv):
    #print_sp500()
    #print_nasdaq100()
    #sys.exit(0)
    opt_long = False
    verbose = False
    debugfifo = False
    output_csv = None
    output_excel = None
    show = False
    try:
        opts, args = getopt.getopt(argv, 'bhluv', ['assume-individual-stock',
            'bmf-force', 'help', 'long', 'output-csv=',
            'output-excel=', 'show', 'tax-output=', 'usd', 'verbose', 'debug-fifo'])
    except getopt.GetoptError:
        usage()
        sys.exit(2)
    for opt, arg in opts:
        if opt == '--assume-individual-stock':
            global assume_stock
            assume_stock = True
        elif opt in ('-b', '--bmf-force'):
            global bmf_force
            bmf_force = True
        elif opt in ('-h', '--help'):
            usage()
            sys.exit()
        elif opt in ('-l', '--long'):
            opt_long = True
        elif opt == '--output-csv':
            output_csv = arg
        elif opt == '--output-excel':
            output_excel = arg
        elif opt in ('-u', '--usd'):
            global convert_currency
            convert_currency = False
        elif opt in ('-v', '--verbose'):
            verbose = True
        elif opt == '--show':
            show = True
        elif opt == '--tax-output':
            global tax_output
            tax_output = arg
        elif opt == '--debug-fifo':
            debugfifo = True
    if len(args) == 0:
        usage()
        sys.exit()
    read_eurusd()
    args.reverse()
    for csv_file in args:
        check_csv(csv_file)
        wk = pandas.read_csv(csv_file, parse_dates=['Date/Time']) # 'Expiration Date'])
        check(wk, output_csv, output_excel, opt_long, verbose, show, debugfifo)

if __name__ == '__main__':
    main(sys.argv[1:])

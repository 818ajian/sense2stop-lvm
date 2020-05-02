#%%
import pymc3 as pm
import arviz as az
import pandas as pd
import numpy as np
from datetime import datetime
import os

# List down file paths
dir_data = "../smoking-lvm-cleaned-data/final"

# Read in data
data_dates = pd.read_csv(os.path.join(os.path.realpath(dir_data), 'participant-dates.csv'))
data_selfreport = pd.read_csv(os.path.join(os.path.realpath(dir_data), 'self-report-smoking-final.csv'))

#%%

###############################################################################
# Data preparation: data_dates data frame
###############################################################################
# Create unix timestamps corresponding to 12AM of a given human-readable date
data_dates["start_date_unixts"] = (
data_dates["start_date"]
    .apply(lambda x: datetime.strptime(x, "%m/%d/%Y"))
    .apply(lambda x: datetime.timestamp(x))
)

data_dates["quit_date_unixts"] = (
data_dates["quit_date"]
    .apply(lambda x: datetime.strptime(x, "%m/%d/%Y"))
    .apply(lambda x: datetime.timestamp(x))
)

data_dates["expected_end_date_unixts"] = (
data_dates["expected_end_date"]
    .apply(lambda x: datetime.strptime(x, "%m/%d/%Y"))
    .apply(lambda x: datetime.timestamp(x))
)

data_dates["actual_end_date_unixts"] = (
data_dates["actual_end_date"]
    .apply(lambda x: datetime.strptime(x, "%m/%d/%Y"))
    .apply(lambda x: datetime.timestamp(x))
)

# More tidying up
data_dates = (
    data_dates
        .rename(columns={"participant": "participant_id"})
        .loc[:, ["participant_id", 
                 "start_date_unixts", "quit_date_unixts",
                 "expected_end_date_unixts","actual_end_date_unixts"]]
)

#%%

###############################################################################
# Merge data_selfreport with data_dates
###############################################################################
data_selfreport = data_dates.merge(data_selfreport, 
                                   how = 'left', 
                                   on = 'participant_id')

#%%

###############################################################################
# Data preparation: data_selfreport data frame
###############################################################################
data_selfreport["begin_unixts"] = data_selfreport["timestamp"]/1000

def calculate_delta(message):
    sr_accptresponse = ['Smoking Event(less than 5 minutes ago)', 
                        'Smoking Event(5 - 15 minutes ago)', 
                        'Smoking Event(15 to 30 minutes ago)']
    sr_dictionary = {'Smoking Event(less than 5 minutes ago)': 2.5, 
                     'Smoking Event(15 - 30 minutes ago)': 17.5, 
                     'Smoking Event(5 - 15 minutes ago)': 10} 

    if message in sr_accptresponse:
        # Convert time from minutes to seconds
        use_delta = sr_dictionary[message]*60  
    else:
        # If participant reported smoking more than 30 minutes ago,
        # then we consider time s/he smoked as missing
        use_delta = pd.NA  
    return use_delta

def round_day(raw_day):
    if pd.isna(raw_day):
        # Missing values for raw_day can occur
        # if participant reported smoking more than 30 minutes ago
        out_day = pd.NA
    else:
        # This takes care of the instances when participant reported to smoke 
        # less than 30 minutes ago
        if raw_day >= 0:
            # If on or after Quit Date, round down to the nearest integer
            # e.g., floor(2.7)=2
            out_day = np.floor(raw_day)
        else:
            # If before Quit Date, round up to the nearest integer
            # e.g., ceil(-2.7)=-2
            out_day = np.ceil(raw_day)
        
    return out_day

#%%

data_selfreport["delta"] = data_selfreport["message"].apply(lambda x: calculate_delta(x))
data_selfreport["smoked_unixts"] = data_selfreport["begin_unixts"] - data_selfreport["delta"]

# Create a new variable, study_day: number of days since participant entered
# the study
data_selfreport["study_day"] = (
        data_selfreport
        .loc[:, ["start_date_unixts","smoked_unixts"]]
        .pipe(lambda x: (x["smoked_unixts"]-x["start_date_unixts"])/(60*60*24))
        .apply(lambda x: round_day(x))
)

# Create a new variable, day_since_quit: number of days before or after 
# 12AM on Quit Date
data_selfreport["day_since_quit"] = (
    data_selfreport
        .loc[:, ["quit_date_unixts","smoked_unixts"]]
        .pipe(lambda x: (x["smoked_unixts"]-x["quit_date_unixts"])/(60*60*24))
        .apply(lambda x: round_day(x))
)

# Drop columns with missing values in the smoked_unixts variable
data_selfreport = data_selfreport.dropna(how = 'any', subset=['smoked_unixts'])
data_selfreport["study_day"] = data_selfreport["study_day"].apply(lambda x: np.int(x))
data_selfreport["day_since_quit"] = data_selfreport["day_since_quit"].apply(lambda x: np.int(x))

# Create a new variable, is_post_quit: whether a given day falls before or on/after 12AM on Quit Date
data_selfreport["is_post_quit"] = data_selfreport["day_since_quit"].apply(lambda x: -1 if x < 0 else 1)

# Create a new variable, day_within_period: 
# if is_post_quit<0, number of days after 12AM on start of study
# if is_post_quit>=0, number of days after 12AM on Quit Date
# hence day_within_period is a count variable with ZERO as minimum value
data_selfreport["day_within_period"] = np.where(data_selfreport["is_post_quit"]<0,
                                                data_selfreport["study_day"], 
                                                data_selfreport["day_since_quit"])

# Finally, select subset of columns
use_these_columns = ["participant_id", "start_date_unixts", "quit_date_unixts",
                     "expected_end_date_unixts","actual_end_date_unixts",
                     "is_post_quit", "study_day", "day_since_quit", "day_within_period",
                     "begin_unixts", "smoked_unixts"]
data_selfreport = data_selfreport.loc[:, use_these_columns]

#%%
###############################################################################
# Exclude individuals before beginning data preparation
###############################################################################
data_selfreport = data_selfreport[data_selfreport['participant_id']!=227]

#%%
###############################################################################
# Data preparation: Create data to be used as input to pymc3
###############################################################################

# Collect data to be used in analyses in a dictionary
collect_data_analysis = {}

data_analysis = (
    data_selfreport
        .loc[:, ['participant_id','study_day','smoked_unixts']]
        .groupby(['participant_id','study_day'])
        .agg('count')
        .reset_index(drop=False)
        .rename(columns={"smoked_unixts": "count"})
)

collect_data_analysis['0'] = data_analysis

data_analysis = (
    data_selfreport
        .loc[:, ['participant_id','is_post_quit','day_within_period','smoked_unixts']]
        .groupby(['participant_id','is_post_quit','day_within_period'])
        .agg('count')
        .reset_index(drop=False)
        .rename(columns={"smoked_unixts": "count"})
)

collect_data_analysis['1'] = data_analysis

# Remove variable from workspace
del data_analysis

# collect_results is a dictionary that will collect results across all models
collect_results={}

#%%

###############################################################################
# Estimation using pymc3
###############################################################################
use_this_data = collect_data_analysis['0']

# Create new participant id's
participant_names = use_this_data['participant_id'].unique()
n_participants = len(participant_names)
d = {'participant_id':participant_names, 'participant_idx':np.array(range(0,n_participants))}
reference_df = pd.DataFrame(d)
use_this_data = use_this_data.merge(reference_df, how = 'left', on = 'participant_id')
participant_idx = use_this_data['participant_idx'].values

#%%
with pm.Model() as model:
    # -------------------------------------------------------------------------
    # Data
    # -------------------------------------------------------------------------
    # Outcome Data
    Y_observed = pm.Data('count', use_this_data['count'].values)

    # -------------------------------------------------------------------------
    # Priors
    # -------------------------------------------------------------------------
    gamma = pm.Normal('gamma', mu=0, sd=10, shape=n_participants)

    # -------------------------------------------------------------------------
    # Likelihood
    # -------------------------------------------------------------------------
    logmu = gamma[participant_idx]
    mu = np.exp(logmu)
    Y_hat = pm.Poisson('Y_hat', mu=mu, observed=Y_observed)

#%%
# Sample from posterior distribution
with model:
    posterior_samples = pm.sample(draws=3000, tune=7000, cores=1)

#%%
# Calculate 95% credible interval
model_summary_logscale = az.summary(posterior_samples, credible_interval=.95)
model_summary_logscale = model_summary_logscale[['mean','hpd_2.5%','hpd_97.5%']]

# Produce trace plots
#pm.traceplot(posterior_samples)

# Transform coefficients and recover mu value
model_summary_expscale = np.exp(model_summary_logscale)
model_summary_expscale = model_summary_expscale.rename(index=lambda x: 'exp('+x+')') 

# Round up to 3 decimal places
model_summary_logscale = model_summary_logscale.round(3)
model_summary_expscale = model_summary_expscale.round(3)

# Collect results
collect_results['0'] = {'model':model, 
                        'posterior_samples':posterior_samples,
                        'model_summary_logscale':model_summary_logscale,
                        'model_summary_expscale':model_summary_expscale}

# Remove variable from workspace
del model, posterior_samples, model_summary_logscale, model_summary_expscale

#%%

###############################################################################
# Estimation using pymc3
###############################################################################
use_this_data = collect_data_analysis['1']

# Create new participant id's
participant_names = use_this_data['participant_id'].unique()
n_participants = len(participant_names)
d = {'participant_id':participant_names, 'participant_idx':np.array(range(0,n_participants))}
reference_df = pd.DataFrame(d)
use_this_data = use_this_data.merge(reference_df, how = 'left', on = 'participant_id')
participant_idx = use_this_data['participant_idx'].values

#%%

with pm.Model() as model:
    # -------------------------------------------------------------------------
    # Data
    # -------------------------------------------------------------------------
    # Outcome Data
    Y_observed = pm.Data('count', use_this_data['count'].values)
    
    # Covariate Data
    is_post_quit = pm.Data('is_post_quit', use_this_data['is_post_quit'].values)
    day_within_period = pm.Data('day_within_period', use_this_data['day_within_period'].values)

    # -------------------------------------------------------------------------
    # Priors
    # -------------------------------------------------------------------------
    beta_prequit = pm.Normal('beta_prequit', mu=0, sd=10)
    beta_postquit = pm.Normal('beta_postquit', mu=0, sd=10)
    gamma_prequit = pm.Normal('gamma_prequit', mu=0, sd=10, shape = n_participants)
    gamma_postquit = pm.Normal('gamma_postquit', mu=0, sd=50, shape = n_participants)
    
    # -------------------------------------------------------------------------
    # Likelihood
    # -------------------------------------------------------------------------
    logmu_prequit = beta_prequit + gamma_prequit[participant_idx]
    mu_prequit = np.exp(logmu_prequit)

    logmu_postquit = beta_postquit + gamma_postquit[participant_idx]
    mu_postquit = np.exp(logmu_postquit)

    mu = pm.math.switch(np.array(is_post_quit == 1), mu_postquit, mu_prequit)

    Y_hat = pm.Poisson('Y_hat', mu=mu, observed=Y_observed)

#%%
# Sample from posterior distribution
with model:
    posterior_samples = pm.sample(draws=12000, tune=8000, cores=1, max_treedepth=20)

#%%
# Calculate 95% credible interval
model_summary_logscale = az.summary(posterior_samples, credible_interval=.95)
model_summary_logscale = model_summary_logscale[['mean','hpd_2.5%','hpd_97.5%']]

# Produce trace plots
#pm.traceplot(posterior_samples)

# Transform coefficients and recover mu value
model_summary_expscale = np.exp(model_summary_logscale)
model_summary_expscale = model_summary_expscale.rename(index=lambda x: 'exp('+x+')') 

# Round up to 3 decimal places
model_summary_logscale = model_summary_logscale.round(3)
model_summary_expscale = model_summary_expscale.round(3)

# Collect results
collect_results['1'] = {'model':model, 
                        'posterior_samples':posterior_samples,
                        'model_summary_logscale':model_summary_logscale,
                        'model_summary_expscale':model_summary_expscale}

# Remove variable from workspace
del model, posterior_samples, model_summary_logscale, model_summary_expscale

#%%

###############################################################################
# Print results from all models
###############################################################################
import matplotlib.pyplot as plt

# Model 0
pm.traceplot(collect_results['0']['posterior_samples'])
print(collect_results['0']['model_summary_logscale'])
print(collect_results['0']['model_summary_expscale'])

plt.figure(figsize=(4,8))
pm.forestplot(collect_results['0']['posterior_samples'], var_names=['gamma'], credible_interval=0.95)

#%%
# Model 1
pm.traceplot(collect_results['1']['posterior_samples'])
print(collect_results['1']['model_summary_logscale'])
print(collect_results['1']['model_summary_expscale'])

plt.figure(figsize=(4,8))
pm.forestplot(collect_results['1']['posterior_samples'], var_names=['gamma_prequit'], credible_interval=0.95)

plt.figure(figsize=(4,8))
pm.forestplot(collect_results['1']['posterior_samples'], var_names=['gamma_postquit'], credible_interval=0.95)

plt.figure(figsize=(4,8))
pm.forestplot(collect_results['1']['posterior_samples'], var_names=['beta_prequit'], credible_interval=0.95)

plt.figure(figsize=(4,8))
pm.forestplot(collect_results['1']['posterior_samples'], var_names=['beta_postquit'], credible_interval=0.95)

# %%
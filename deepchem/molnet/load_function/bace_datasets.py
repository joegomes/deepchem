"""
bace dataset loader.
"""
from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals

import os
import deepchem
from deepchem.molnet.load_function.bace_features import bace_user_specified_features


def load_bace_regression(featurizer=None, split='random'):
  """Load bace datasets."""
  # Featurize bace dataset
  print("About to featurize bace dataset.")
  if "DEEPCHEM_DATA_DIR" in os.environ:
    data_dir = os.environ["DEEPCHEM_DATA_DIR"]
  else:
    data_dir = "/tmp"

  dataset_file = os.path.join(data_dir, "bace.csv")

  if not os.path.exists(dataset_file):
    os.system(
        'wget -P ' + data_dir +
        ' http://deepchem.io.s3-website-us-west-1.amazonaws.com/datasets/bace.csv '
    )

  bace_tasks = ["pIC50"]
  if featurizer == 'ECFP':
    featurizer = deepchem.feat.CircularFingerprint(size=1024)
  elif featurizer == 'GraphConv':
    featurizer = deepchem.feat.ConvMolFeaturizer()
  elif featurizer == 'Raw':
    featurizer = deepchem.feat.RawFeaturizer()
  elif featurizer == None:
    featurizer = deepchem.feat.UserDefinedFeaturizer(
        bace_user_specified_features)

  loader = deepchem.data.CSVLoader(
      tasks=bace_tasks, smiles_field="mol", featurizer=featurizer)

  dataset = loader.featurize(dataset_file, shard_size=8192)
  # Initialize transformers 
  transformers = [
      deepchem.trans.NormalizationTransformer(
          transform_y=True, dataset=dataset)
  ]

  print("About to transform data")
  for transformer in transformers:
    dataset = transformer.transform(dataset)

  splitters = {
      'index': deepchem.splits.IndexSplitter(),
      'random': deepchem.splits.RandomSplitter(),
      'scaffold': deepchem.splits.ScaffoldSplitter()
  }
  splitter = splitters[split]
  train, valid, test = splitter.train_valid_test_split(dataset)
  return bace_tasks, (train, valid, test), transformers


def load_bace_classification(featurizer=None, split='random'):
  """Load bace datasets."""
  # Featurize bace dataset
  print("About to featurize bace dataset.")
  if "DEEPCHEM_DATA_DIR" in os.environ:
    data_dir = os.environ["DEEPCHEM_DATA_DIR"]
  else:
    data_dir = "/tmp"

  dataset_file = os.path.join(data_dir, "bace.csv")

  if not os.path.exists(dataset_file):
    os.system(
        'wget -P ' + data_dir +
        ' http://deepchem.io.s3-website-us-west-1.amazonaws.com/datasets/bace.csv '
    )

  bace_tasks = ["Class"]
  if featurizer == 'ECFP':
    featurizer = deepchem.feat.CircularFingerprint(size=1024)
  elif featurizer == 'GraphConv':
    featurizer = deepchem.feat.ConvMolFeaturizer()
  elif featurizer == 'Raw':
    featurizer = deepchem.feat.RawFeaturizer()
  elif featurizer == None:
    featurizer = deepchem.feat.UserDefinedFeaturizer(
        bace_user_specified_features)

  loader = deepchem.data.CSVLoader(
      tasks=bace_tasks, smiles_field="mol", featurizer=featurizer)

  dataset = loader.featurize(dataset_file, shard_size=8192)
  # Initialize transformers 
  transformers = [
      deepchem.trans.BalancingTransformer(transform_w=True, dataset=dataset)
  ]

  print("About to transform data")
  for transformer in transformers:
    dataset = transformer.transform(dataset)

  splitters = {
      'index': deepchem.splits.IndexSplitter(),
      'random': deepchem.splits.RandomSplitter(),
      'scaffold': deepchem.splits.ScaffoldSplitter()
  }
  splitter = splitters[split]
  train, valid, test = splitter.train_valid_test_split(dataset)
  return bace_tasks, (train, valid, test), transformers

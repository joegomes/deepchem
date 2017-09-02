import random
import string
from collections import Sequence

import tensorflow as tf
import numpy as np

from deepchem.nn import model_ops, initializations, regularizers, activations


class Layer(object):
  layer_number_dict = {}

  def __init__(self, in_layers=None, **kwargs):
    if "name" in kwargs:
      self.name = kwargs['name']
    else:
      self.name = None
    if in_layers is None:
      in_layers = list()
    if not isinstance(in_layers, Sequence):
      in_layers = [in_layers]
    self.in_layers = in_layers
    self.op_type = "gpu"
    self.variable_scope = ''
    self.rnn_initial_states = []
    self.rnn_final_states = []
    self.rnn_zero_states = []
    self.tensorboard = False
    self.tb_input = None

  def _get_layer_number(self):
    class_name = self.__class__.__name__
    if class_name not in Layer.layer_number_dict:
      Layer.layer_number_dict[class_name] = 0
    Layer.layer_number_dict[class_name] += 1
    return "%s" % Layer.layer_number_dict[class_name]

  def none_tensors(self):
    out_tensor = self.out_tensor
    self.out_tensor = None
    return out_tensor

  def set_tensors(self, tensor):
    self.out_tensor = tensor

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    raise NotImplementedError("Subclasses must implement for themselves")

  def shared(self, in_layers):
    """
    Share weights with different in tensors and a new out tensor
    Parameters
    ----------
    in_layers: list tensor
    List in tensors for the shared layer

    Returns
    -------
    Layer
    """
    raise NotImplementedError("Each Layer must implement shared for itself")

  def __call__(self, *in_layers):
    return self.create_tensor(in_layers=in_layers, set_tensors=False)

  @property
  def shape(self):
    """Get the shape of this Layer's output."""
    if '_shape' not in dir(self):
      raise NotImplementedError(
          "%s: shape is not known" % self.__class__.__name__)
    return self._shape

  def _get_input_tensors(self, in_layers, reshape=False):
    """Get the input tensors to his layer.

    Parameters
    ----------
    in_layers: list of Layers or tensors
      the inputs passed to create_tensor().  If None, this layer's inputs will
      be used instead.
    reshape: bool
      if True, try to reshape the inputs to all have the same shape
    """
    if in_layers is None:
      in_layers = self.in_layers
    if not isinstance(in_layers, Sequence):
      in_layers = [in_layers]
    tensors = []
    for input in in_layers:
      tensors.append(tf.convert_to_tensor(input))
    if reshape and len(tensors) > 1:
      shapes = [t.get_shape() for t in tensors]
      if any(s != shapes[0] for s in shapes[1:]):
        # Reshape everything to match the input with the most dimensions.

        shape = shapes[0]
        for s in shapes:
          if len(s) > len(shape):
            shape = s
        shape = [-1 if x is None else x for x in shape.as_list()]
        for i in range(len(tensors)):
          tensors[i] = tf.reshape(tensors[i], shape)
    return tensors

  def _record_variable_scope(self, local_scope):
    """Record the scope name used for creating variables.

    This should be called from create_tensor().  It allows the list of variables
    belonging to this layer to be retrieved later."""
    parent_scope = tf.get_variable_scope().name
    if len(parent_scope) > 0:
      self.variable_scope = '%s/%s' % (parent_scope, local_scope)
    else:
      self.variable_scope = local_scope

  def set_summary(self, summary_op, summary_description=None, collections=None):
    """Annotates a tensor with a tf.summary operation
    Collects data from self.out_tensor by default but can be changed by setting 
    self.tb_input to another tensor in create_tensor


    Parameters
    ----------
    summary_op: str
      summary operation to annotate node
    summary_description: object, optional
      Optional summary_pb2.SummaryDescription()
    collections: list of graph collections keys, optional
      New summary op is added to these collections. Defaults to [GraphKeys.SUMMARIES]
    """
    supported_ops = {'tensor_summary', 'scalar', 'histogram'}
    if summary_op not in supported_ops:
      raise ValueError(
          "Invalid summary_op arg. Only 'tensor_summary', 'scalar', 'histogram' supported"
      )
    self.summary_op = summary_op
    self.summary_description = summary_description
    self.collections = collections
    self.tensorboard = True

  def add_summary_to_tg(self):
    """
    Can only be called after self.create_layer to gaurentee that name is not none
    """
    if self.tensorboard == False:
      return
    if self.tb_input == None:
      self.tb_input = self.out_tensor
    if self.summary_op == "tensor_summary":
      tf.summary.tensor_summary(self.name, self.tb_input,
                                self.summary_description, self.collections)
    elif self.summary_op == 'scalar':
      tf.summary.scalar(self.name, self.tb_input, self.collections)
    elif self.summary_op == 'histogram':
      tf.summary.histogram(self.name, self.tb_input, self.collections)

  def _as_graph_element(self):
    if '_as_graph_element' in dir(self.out_tensor):
      return self.out_tensor._as_graph_element()
    else:
      return self.out_tensor


def _convert_layer_to_tensor(value, dtype=None, name=None, as_ref=False):
  return tf.convert_to_tensor(value.out_tensor, dtype=dtype, name=name)


tf.register_tensor_conversion_function(Layer, _convert_layer_to_tensor)


class TensorWrapper(Layer):
  """Used to wrap a tensorflow tensor."""

  def __init__(self, out_tensor, **kwargs):
    self.out_tensor = out_tensor
    self._shape = out_tensor.get_shape().as_list()
    super(TensorWrapper, self).__init__(**kwargs)

  def create_tensor(self, in_layers=None, **kwargs):
    """Take no actions."""
    pass


def convert_to_layers(in_layers):
  """Wrap all inputs into tensors if necessary."""
  layers = []
  for in_layer in in_layers:
    if isinstance(in_layer, Layer):
      layers.append(in_layer)
    elif isinstance(in_layer, tf.Tensor):
      layers.append(TensorWrapper(in_layer))
    else:
      raise ValueError("convert_to_layers must be invoked on layers or tensors")
  return layers


class Conv1D(Layer):
  """A 1D convolution on the input.

  This layer expects its input to be a three dimensional tensor of shape (batch size, width, # channels).
  If there is only one channel, the third dimension may optionally be omitted.
  """

  def __init__(self,
               width,
               out_channels,
               stride=1,
               padding='SAME',
               activation_fn=tf.nn.relu,
               **kwargs):
    """Create a Conv1D layer.

    Parameters
    ----------
    width: int
      the width of the convolutional kernel
    out_channels: int
      the number of outputs produced by the convolutional kernel
    stride: int
      the stride between applications of the convolutional kernel
    padding: str
      the padding method to use, either 'SAME' or 'VALID'
    activation_fn: object
      the Tensorflow activation function to apply to the output
    """
    self.width = width
    self.out_channels = out_channels
    self.stride = stride
    self.padding = padding
    self.activation_fn = activation_fn
    self.out_tensor = None
    super(Conv1D, self).__init__(**kwargs)
    try:
      parent_shape = self.in_layers[0].shape
      self._shape = (parent_shape[0], parent_shape[1] // stride, out_channels)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) != 1:
      raise ValueError("Conv1D layer must have exactly one parent")
    parent = inputs[0]
    if len(parent.get_shape()) == 2:
      parent = tf.expand_dims(parent, 2)
    elif len(parent.get_shape()) != 3:
      raise ValueError("Parent tensor must be (batch, width, channel)")
    parent_shape = parent.get_shape()
    parent_channel_size = parent_shape[2].value
    f = tf.Variable(
        tf.random_normal([self.width, parent_channel_size, self.out_channels]))
    b = tf.Variable(tf.random_normal([self.out_channels]))
    t = tf.nn.conv1d(parent, f, stride=self.stride, padding=self.padding)
    t = tf.nn.bias_add(t, b)
    out_tensor = self.activation_fn(t)
    if set_tensors:
      self._record_variable_scope(self.name)
      self.out_tensor = out_tensor
    return out_tensor


class Dense(Layer):

  def __init__(
      self,
      out_channels,
      activation_fn=None,
      biases_initializer=tf.zeros_initializer,
      weights_initializer=tf.contrib.layers.variance_scaling_initializer,
      time_series=False,
      **kwargs):
    """Create a dense layer.

    The weight and bias initializers are specified by callable objects that construct
    and return a Tensorflow initializer when invoked with no arguments.  This will typically
    be either the initializer class itself (if the constructor does not require arguments),
    or a TFWrapper (if it does).

    Parameters
    ----------
    out_channels: int
      the number of output values
    activation_fn: object
      the Tensorflow activation function to apply to the output
    biases_initializer: callable object
      the initializer for bias values.  This may be None, in which case the layer
      will not include biases.
    weights_initializer: callable object
      the initializer for weight values
    time_series: bool
      if True, the dense layer is applied to each element of a batch in sequence
    """
    super(Dense, self).__init__(**kwargs)
    self.out_channels = out_channels
    self.out_tensor = None
    self.activation_fn = activation_fn
    self.biases_initializer = biases_initializer
    self.weights_initializer = weights_initializer
    self.time_series = time_series
    try:
      parent_shape = self.in_layers[0].shape
      if len(parent_shape) == 2:
        self._shape = (parent_shape[0], out_channels)
      elif len(parent_shape) == 3:
        self._shape = (parent_shape[0], parent_shape[1], out_channels)
    except:
      pass
    self._reuse = False
    self._shared_with = None

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) != 1:
      raise ValueError("Dense layer can only have one input")
    parent = inputs[0]
    if self.biases_initializer is None:
      biases_initializer = None
    else:
      biases_initializer = self.biases_initializer()
    for reuse in (self._reuse, False):
      dense_fn = lambda x: tf.contrib.layers.fully_connected(x,
                                                             num_outputs=self.out_channels,
                                                             activation_fn=self.activation_fn,
                                                             biases_initializer=biases_initializer,
                                                             weights_initializer=self.weights_initializer(),
                                                             scope=self._get_scope_name(),
                                                             reuse=reuse,
                                                             trainable=True)
      try:
        if self.time_series:
          out_tensor = tf.map_fn(dense_fn, parent)
        else:
          out_tensor = dense_fn(parent)
        break
      except ValueError:
        if reuse:
          # This probably means the variable hasn't been created yet, so try again
          # with reuse set to false.
          continue
        raise
    if set_tensors:
      self._record_variable_scope(self._get_scope_name())
      self.out_tensor = out_tensor
    return out_tensor

  def shared(self, in_layers):
    copy = Dense(
        self.out_channels,
        self.activation_fn,
        self.biases_initializer,
        self.weights_initializer,
        time_series=self.time_series,
        in_layers=in_layers)
    self._reuse = True
    copy._reuse = True
    copy._shared_with = self
    return copy

  def _get_scope_name(self):
    if self._shared_with is None:
      return self.name
    else:
      return self._shared_with._get_scope_name()


class Flatten(Layer):
  """Flatten every dimension except the first"""

  def __init__(self, **kwargs):
    super(Flatten, self).__init__(**kwargs)
    try:
      parent_shape = self.in_layers[0].shape
      s = list(parent_shape[:2])
      for x in parent_shape[2:]:
        s[1] *= x
      self._shape = tuple(s)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) != 1:
      raise ValueError("Only One Parent to Flatten")
    parent = inputs[0]
    parent_shape = parent.get_shape()
    vector_size = 1
    for i in range(1, len(parent_shape)):
      vector_size *= parent_shape[i].value
    parent_tensor = parent
    out_tensor = tf.reshape(parent_tensor, shape=(-1, vector_size))
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class Reshape(Layer):

  def __init__(self, shape, **kwargs):
    super(Reshape, self).__init__(**kwargs)
    self._new_shape = tuple(-1 if x is None else x for x in shape)
    try:
      parent_shape = self.in_layers[0].shape
      s = tuple(None if x == -1 else x for x in shape)
      if None in parent_shape or None not in s:
        self._shape = s
      else:
        # Calculate what the new shape will be.
        t = 1
        for x in parent_shape:
          t *= x
        for x in s:
          if x is not None:
            t //= x
        self._shape = tuple(t if x is None else x for x in s)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    parent_tensor = inputs[0]
    out_tensor = tf.reshape(parent_tensor, self._new_shape)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class Squeeze(Layer):

  def __init__(self, squeeze_dims=None, **kwargs):
    self.squeeze_dims = squeeze_dims
    super(Squeeze, self).__init__(**kwargs)
    try:
      parent_shape = self.in_layers[0].shape
      if squeeze_dims is None:
        self._shape = [i for i in parent_shape if i != 1]
      else:
        self._shape = [
            parent_shape[i] for i in range(len(parent_shape))
            if i not in squeeze_dims
        ]
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    parent_tensor = inputs[0]
    out_tensor = tf.squeeze(parent_tensor, squeeze_dims=self.squeeze_dims)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class Transpose(Layer):

  def __init__(self, perm, **kwargs):
    super(Transpose, self).__init__(**kwargs)
    self.perm = perm
    try:
      parent_shape = self.in_layers[0].shape
      self._shape = tuple(parent_shape[i] for i in perm)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) != 1:
      raise ValueError("Only One Parent to Transpose over")
    out_tensor = tf.transpose(inputs[0], self.perm)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor

class ReLU(Layer):

  def __init__(self, **kwargs):
    super(ReLU, self).__init__(**kwargs)

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) != 1:
      raise ValueError("Only One Parent to ReLU")
    out_tensor = tf.nn.relu(inputs[0])
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class CombineMeanStd(Layer):

  def __init__(self, **kwargs):
    super(CombineMeanStd, self).__init__(**kwargs)
    try:
      self._shape = self.in_layers[0].shape
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) != 2:
      raise ValueError("Must have two in_layers")
    mean_parent, std_parent = inputs[0], inputs[1]
    sample_noise = tf.random_normal(
        mean_parent.get_shape(), 0, 1, dtype=tf.float32)
    out_tensor = mean_parent + (std_parent * sample_noise)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class Repeat(Layer):

  def __init__(self, n_times, **kwargs):
    self.n_times = n_times
    super(Repeat, self).__init__(**kwargs)
    try:
      s = list(self.in_layers[0].shape)
      s.insert(1, n_times)
      self._shape = tuple(s)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) != 1:
      raise ValueError("Must have one parent")
    parent_tensor = inputs[0]
    t = tf.expand_dims(parent_tensor, 1)
    pattern = tf.stack([1, self.n_times, 1])
    out_tensor = tf.tile(t, pattern)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class GRU(Layer):
  """A Gated Recurrent Unit.

  This layer expects its input to be of shape (batch_size, sequence_length, ...).
  It consists of a set of independent sequence (one for each element in the batch),
  that are each propagated independently through the GRU.
  """

  def __init__(self, n_hidden, batch_size, **kwargs):
    """Create a Gated Recurrent Unit.

    Parameters
    ----------
    n_hidden: int
      the size of the GRU's hidden state, which also determines the size of its output
    batch_size: int
      the batch size that will be used with this layer
    """
    self.n_hidden = n_hidden
    self.batch_size = batch_size
    super(GRU, self).__init__(**kwargs)
    try:
      parent_shape = self.in_layers[0].shape
      self._shape = (batch_size, parent_shape[1], n_hidden)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) != 1:
      raise ValueError("Must have one parent")
    parent_tensor = inputs[0]
    gru_cell = tf.contrib.rnn.GRUCell(self.n_hidden)
    zero_state = gru_cell.zero_state(self.batch_size, tf.float32)
    if set_tensors:
      initial_state = tf.placeholder(tf.float32, zero_state.get_shape())
    else:
      initial_state = zero_state
    out_tensor, final_state = tf.nn.dynamic_rnn(
        gru_cell, parent_tensor, initial_state=initial_state, scope=self.name)
    if set_tensors:
      self._record_variable_scope(self.name)
      self.out_tensor = out_tensor
      self.rnn_initial_states.append(initial_state)
      self.rnn_final_states.append(final_state)
      self.rnn_zero_states.append(np.zeros(zero_state.get_shape(), np.float32))
    return out_tensor

  def none_tensors(self):
    saved_tensors = [
        self.out_tensor, self.rnn_initial_states, self.rnn_final_states,
        self.rnn_zero_states
    ]
    self.out_tensor = None
    self.rnn_initial_states = []
    self.rnn_final_states = []
    self.rnn_zero_states = []
    return saved_tensors

  def set_tensors(self, tensor):
    self.out_tensor, self.rnn_initial_states, self.rnn_final_states, self.rnn_zero_states = tensor


class TimeSeriesDense(Layer):

  def __init__(self, out_channels, **kwargs):
    self.out_channels = out_channels
    super(TimeSeriesDense, self).__init__(**kwargs)

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) != 1:
      raise ValueError("Must have one parent")
    parent_tensor = inputs[0]
    dense_fn = lambda x: tf.contrib.layers.fully_connected(
        x, num_outputs=self.out_channels,
        activation_fn=tf.nn.sigmoid)
    out_tensor = tf.map_fn(dense_fn, parent_tensor)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class Input(Layer):

  def __init__(self, shape, dtype=tf.float32, **kwargs):
    self._shape = tuple(shape)
    self.dtype = dtype
    super(Input, self).__init__(**kwargs)
    self.op_type = "cpu"

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    if in_layers is None:
      in_layers = self.in_layers
    in_layers = convert_to_layers(in_layers)
    if len(in_layers) > 0:
      queue = in_layers[0]
      placeholder = queue.out_tensors[self.get_pre_q_name()]
      self.out_tensor = tf.placeholder_with_default(placeholder, self._shape)
      return self.out_tensor
    out_tensor = tf.placeholder(dtype=self.dtype, shape=self._shape)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor

  def create_pre_q(self, batch_size):
    q_shape = (batch_size,) + self._shape[1:]
    return Input(shape=q_shape, name="%s_pre_q" % self.name, dtype=self.dtype)

  def get_pre_q_name(self):
    return "%s_pre_q" % self.name


class Feature(Input):

  def __init__(self, **kwargs):
    super(Feature, self).__init__(**kwargs)


class Label(Input):

  def __init__(self, **kwargs):
    super(Label, self).__init__(**kwargs)


class Weights(Input):

  def __init__(self, **kwargs):
    super(Weights, self).__init__(**kwargs)


class L1Loss(Layer):

  def __init__(self, **kwargs):
    super(L1Loss, self).__init__(**kwargs)

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers, True)
    guess, label = inputs[0], inputs[1]
    out_tensor = tf.reduce_mean(
        tf.abs(guess - label), axis=list(range(1, len(label.shape))))
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class L2Loss(Layer):

  def __init__(self, **kwargs):
    super(L2Loss, self).__init__(**kwargs)
    try:
      shape1 = self.in_layers[0].shape
      shape2 = self.in_layers[1].shape
      if shape1[0] is None:
        self._shape = (parent_shape[1],)
      else:
        self._shape = (parent_shape[0],)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers, True)
    guess, label = inputs[0], inputs[1]
    out_tensor = tf.reduce_mean(
        tf.square(guess - label), axis=list(range(1, len(label._shape))))
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class SoftMax(Layer):

  def __init__(self, **kwargs):
    super(SoftMax, self).__init__(**kwargs)
    try:
      self._shape = tuple(self.in_layers[0].shape)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) != 1:
      raise ValueError("Must only Softmax single parent")
    parent = inputs[0]
    out_tensor = tf.contrib.layers.softmax(parent)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class Concat(Layer):

  def __init__(self, axis=1, **kwargs):
    self.axis = axis
    super(Concat, self).__init__(**kwargs)
    try:
      s = list(self.in_layers[0].shape)
      for parent in self.in_layers[1:]:
        s[axis] += parent.shape[axis]
      self._shape = tuple(s)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) == 1:
      self.out_tensor = inputs[0]
      return self.out_tensor

    out_tensor = tf.concat(inputs, axis=self.axis)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class Stack(Layer):

  def __init__(self, axis=1, **kwargs):
    self.axis = axis
    super(Stack, self).__init__(**kwargs)
    try:
      s = list(self.in_layers[0].shape)
      s.insert(axis, len(self.in_layers))
      self._shape = tuple(s)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    out_tensor = tf.stack(inputs, axis=self.axis)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class Constant(Layer):
  """Output a constant value."""

  def __init__(self, value, dtype=tf.float32, **kwargs):
    """Construct a constant layer.

    Parameters
    ----------
    value: array
      the value the layer should output
    dtype: tf.DType
      the data type of the output value.
    """
    if not isinstance(value, np.ndarray):
      value = np.array(value)
    self.value = value
    self.dtype = dtype
    self._shape = tuple(value.shape)
    super(Constant, self).__init__(**kwargs)

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    out_tensor = tf.constant(self.value, dtype=self.dtype)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class Variable(Layer):
  """Output a trainable value."""

  def __init__(self, initial_value, dtype=tf.float32, **kwargs):
    """Construct a variable layer.

    Parameters
    ----------
    initial_value: array
      the initial value the layer should output
    dtype: tf.DType
      the data type of the output value.
    """
    if not isinstance(initial_value, np.ndarray):
      initial_value = np.array(initial_value)
    self.initial_value = initial_value
    self.dtype = dtype
    self._shape = tuple(initial_value.shape)
    super(Variable, self).__init__(**kwargs)

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    out_tensor = tf.Variable(self.initial_value, dtype=self.dtype)
    if set_tensors:
      self._record_variable_scope(self.name)
      self.out_tensor = out_tensor
    return out_tensor


class Add(Layer):
  """Compute the (optionally weighted) sum of the input layers."""

  def __init__(self, weights=None, **kwargs):
    """Create an Add layer.

    Parameters
    ----------
    weights: array
      an array of length equal to the number of input layers, giving the weight
      to multiply each input by.  If None, all weights are set to 1.
    """
    super(Add, self).__init__(**kwargs)
    self.weights = weights
    try:
      shape1 = list(self.in_layers[0].shape)
      shape2 = list(self.in_layers[1].shape)
      if len(shape1) < len(shape2):
        shape2, shape1 = shape1, shape2
      offset = len(shape1) - len(shape2)
      for i in range(len(shape2)):
        shape1[i + offset] = max(shape1[i + offset], shape2[i])
      self._shape = tuple(shape1)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    weights = self.weights
    if weights is None:
      weights = [1] * len(inputs)
    out_tensor = inputs[0]
    if weights[0] != 1:
      out_tensor *= weights[0]
    for layer, weight in zip(inputs[1:], weights[1:]):
      if weight == 1:
        out_tensor += layer
      else:
        out_tensor += weight * layer
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class Multiply(Layer):
  """Compute the product of the input layers."""

  def __init__(self, **kwargs):
    super(Multiply, self).__init__(**kwargs)
    try:
      shape1 = list(self.in_layers[0].shape)
      shape2 = list(self.in_layers[1].shape)
      if len(shape1) < len(shape2):
        shape2, shape1 = shape1, shape2
      offset = len(shape1) - len(shape2)
      for i in range(len(shape2)):
        shape1[i + offset] = max(shape1[i + offset], shape2[i])
      self._shape = tuple(shape1)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    out_tensor = inputs[0]
    for layer in inputs[1:]:
      out_tensor *= layer
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class InteratomicL2Distances(Layer):
  """Compute (squared) L2 Distances between atoms given neighbors."""

  def __init__(self, N_atoms, M_nbrs, ndim, **kwargs):
    self.N_atoms = N_atoms
    self.M_nbrs = M_nbrs
    self.ndim = ndim
    super(InteratomicL2Distances, self).__init__(**kwargs)

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) != 2:
      raise ValueError("InteratomicDistances requires coords,nbr_list")
    coords, nbr_list = (inputs[0], inputs[1])
    N_atoms, M_nbrs, ndim = self.N_atoms, self.M_nbrs, self.ndim
    # Shape (N_atoms, M_nbrs, ndim)
    nbr_coords = tf.gather(coords, nbr_list)
    # Shape (N_atoms, M_nbrs, ndim)
    tiled_coords = tf.tile(
        tf.reshape(coords, (N_atoms, 1, ndim)), (1, M_nbrs, 1))
    # Shape (N_atoms, M_nbrs)
    dists = tf.reduce_sum((tiled_coords - nbr_coords)**2, axis=2)
    out_tensor = dists
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class SparseSoftMaxCrossEntropy(Layer):

  def __init__(self, **kwargs):
    super(SparseSoftMaxCrossEntropy, self).__init__(**kwargs)
    try:
      self._shape = (self.in_layers[1].shape[0], 1)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers, False)
    if len(inputs) != 2:
      raise ValueError()
    labels, logits = inputs[0], inputs[1]
    self.out_tensor = tf.nn.sparse_softmax_cross_entropy_with_logits(
        logits=logits, labels=labels)
    out_tensor = tf.reshape(self.out_tensor, [-1, 1])
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class SoftMaxCrossEntropy(Layer):

  def __init__(self, **kwargs):
    super(SoftMaxCrossEntropy, self).__init__(**kwargs)
    try:
      self._shape = (self.in_layers[1].shape[0], 1)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers, True)
    if len(inputs) != 2:
      raise ValueError()
    labels, logits = inputs[0], inputs[1]
    self.out_tensor = tf.nn.softmax_cross_entropy_with_logits(
        logits=logits, labels=labels)
    out_tensor = tf.reshape(self.out_tensor, [-1, 1])
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class ReduceMean(Layer):

  def __init__(self, axis=None, **kwargs):
    self.axis = axis
    super(ReduceMean, self).__init__(**kwargs)
    if axis is None:
      self._shape = tuple()
    else:
      try:
        parent_shape = self.in_layers[0].shape
        self._shape = [
            parent_shape[i] for i in range(len(parent_shape)) if i not in axis
        ]
      except:
        pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) > 1:
      self.out_tensor = tf.stack(inputs)
    else:
      self.out_tensor = inputs[0]

    out_tensor = tf.reduce_mean(self.out_tensor, axis=self.axis)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class ToFloat(Layer):

  def __init__(self, **kwargs):
    super(ToFloat, self).__init__(**kwargs)
    try:
      self._shape = tuple(self.in_layers[0].shape)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) > 1:
      raise ValueError("Only one layer supported.")
    out_tensor = tf.to_float(inputs[0])
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class ReduceSum(Layer):

  def __init__(self, axis=None, **kwargs):
    self.axis = axis
    super(ReduceSum, self).__init__(**kwargs)
    if axis is None:
      self._shape = tuple()
    else:
      try:
        parent_shape = self.in_layers[0].shape
        self._shape = [
            parent_shape[i] for i in range(len(parent_shape)) if i not in axis
        ]
      except:
        pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) > 1:
      self.out_tensor = tf.stack(inputs)
    else:
      self.out_tensor = inputs[0]

    out_tensor = tf.reduce_sum(self.out_tensor, axis=self.axis)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class ReduceSquareDifference(Layer):

  def __init__(self, axis=None, **kwargs):
    self.axis = axis
    super(ReduceSquareDifference, self).__init__(**kwargs)
    if axis is None:
      self._shape = tuple()
    else:
      try:
        parent_shape = self.in_layers[0].shape
        self._shape = [
            parent_shape[i] for i in range(len(parent_shape)) if i not in axis
        ]
      except:
        pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers, True)
    a = inputs[0]
    b = inputs[1]
    out_tensor = tf.reduce_mean(tf.squared_difference(a, b), axis=self.axis)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class Conv2D(Layer):
  """A 2D convolution on the input.

  This layer expects its input to be a four dimensional tensor of shape (batch size, height, width, # channels).
  If there is only one channel, the fourth dimension may optionally be omitted.
  """

  def __init__(self,
               num_outputs,
               kernel_size=5,
               stride=1,
               padding='SAME',
               activation_fn=tf.nn.relu,
               normalizer_fn=None,
               scope_name=None,
               **kwargs):
    """Create a Conv2D layer.

    Parameters
    ----------
    num_outputs: int
      the number of outputs produced by the convolutional kernel
    kernel_size: int or tuple
      the width of the convolutional kernel.  This can be either a two element tuple, giving
      the kernel size along each dimension, or an integer to use the same size along both
      dimensions.
    stride: int or tuple
      the stride between applications of the convolutional kernel.  This can be either a two
      element tuple, giving the stride along each dimension, or an integer to use the same
      stride along both dimensions.
    padding: str
      the padding method to use, either 'SAME' or 'VALID'
    activation_fn: object
      the Tensorflow activation function to apply to the output
    normalizer_fn: object
      the Tensorflow normalizer function to apply to the output
    """
    self.num_outputs = num_outputs
    self.kernel_size = kernel_size
    self.stride = stride
    self.padding = padding
    self.activation_fn = activation_fn
    self.normalizer_fn = normalizer_fn
    super(Conv2D, self).__init__(**kwargs)
    if scope_name is None:
      scope_name = self.name
    self.scope_name = scope_name
    try:
      parent_shape = self.in_layers[0].shape
      strides = stride
      if isinstance(stride, int):
        strides = (stride, stride)
      self._shape = (parent_shape[0], parent_shape[1] // strides[0],
                     parent_shape[2] // strides[1], num_outputs)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    parent_tensor = inputs[0]
    if len(parent_tensor.get_shape()) == 3:
      parent_tensor = tf.expand_dims(parent_tensor, 3)
    out_tensor = tf.contrib.layers.conv2d(
        parent_tensor,
        num_outputs=self.num_outputs,
        kernel_size=self.kernel_size,
        stride=self.stride,
        padding=self.padding,
        activation_fn=self.activation_fn,
        normalizer_fn=self.normalizer_fn,
        scope=self.scope_name)
    #out_tensor = out_tensor
    if set_tensors:
      self._record_variable_scope(self.scope_name)
      self.out_tensor = out_tensor
    return out_tensor


class MaxPool(Layer):

  def __init__(self,
               ksize=[1, 2, 2, 1],
               strides=[1, 2, 2, 1],
               padding="SAME",
               **kwargs):
    self.ksize = ksize
    self.strides = strides
    self.padding = padding
    super(MaxPool, self).__init__(**kwargs)
    try:
      parent_shape = self.in_layers[0].shape
      self._shape = tuple(None if p is None else p // s
                          for p, s in zip(parent_shape, strides))
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    in_tensor = inputs[0]
    out_tensor = tf.nn.max_pool(
        in_tensor, ksize=self.ksize, strides=self.strides, padding=self.padding)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class InputFifoQueue(Layer):
  """
  This Queue Is used to allow asynchronous batching of inputs
  During the fitting process
  """

  def __init__(self, shapes, names, capacity=5, **kwargs):
    self.shapes = shapes
    self.names = names
    self.capacity = capacity
    super(InputFifoQueue, self).__init__(**kwargs)

  def create_tensor(self, in_layers=None, **kwargs):
    # TODO(rbharath): Note sure if this layer can be called with __call__
    # meaningfully, so not going to support that functionality for now.
    if in_layers is None:
      in_layers = self.in_layers
    in_layers = convert_to_layers(in_layers)
    self.dtypes = [x.out_tensor.dtype for x in in_layers]
    self.queue = tf.FIFOQueue(
        self.capacity, self.dtypes, shapes=self.shapes, names=self.names)
    feed_dict = {x.name: x.out_tensor for x in in_layers}
    self.out_tensor = self.queue.enqueue(feed_dict)
    self.close_op = self.queue.close()
    self.out_tensors = self.queue.dequeue()

  def none_tensors(self):
    queue, out_tensors, out_tensor, close_op = self.queue, self.out_tensor, self.out_tensor, self.close_op
    self.queue, self.out_tensor, self.out_tensors, self.close_op = None, None, None, None
    return queue, out_tensors, out_tensor, close_op

  def set_tensors(self, tensors):
    self.queue, self.out_tensor, self.out_tensors, self.close_op = tensors

  def close(self):
    self.queue.close()


class GraphConv(Layer):

  def __init__(self,
               out_channel,
               min_deg=0,
               max_deg=10,
               activation_fn=None,
               **kwargs):
    self.out_channel = out_channel
    self.min_degree = min_deg
    self.max_degree = max_deg
    self.num_deg = 2 * max_deg + (1 - min_deg)
    self.activation_fn = activation_fn
    super(GraphConv, self).__init__(**kwargs)

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    # in_layers = [atom_features, deg_slice, membership, deg_adj_list placeholders...]
    in_channels = inputs[0].get_shape()[-1].value

    # Generate the nb_affine weights and biases
    self.W_list = [
        initializations.glorot_uniform([in_channels, self.out_channel])
        for k in range(self.num_deg)
    ]
    self.b_list = [
        model_ops.zeros(shape=[
            self.out_channel,
        ]) for k in range(self.num_deg)
    ]

    # Extract atom_features
    atom_features = inputs[0]

    # Extract graph topology
    deg_slice = inputs[1]
    deg_adj_lists = inputs[3:]

    # Perform the mol conv
    # atom_features = graph_conv(atom_features, deg_adj_lists, deg_slice,
    #                            self.max_deg, self.min_deg, self.W_list,
    #                            self.b_list)

    W = iter(self.W_list)
    b = iter(self.b_list)

    # Sum all neighbors using adjacency matrix
    deg_summed = self.sum_neigh(atom_features, deg_adj_lists)

    # Get collection of modified atom features
    new_rel_atoms_collection = (self.max_degree + 1 - self.min_degree) * [None]

    for deg in range(1, self.max_degree + 1):
      # Obtain relevant atoms for this degree
      rel_atoms = deg_summed[deg - 1]

      # Get self atoms
      begin = tf.stack([deg_slice[deg - self.min_degree, 0], 0])
      size = tf.stack([deg_slice[deg - self.min_degree, 1], -1])
      self_atoms = tf.slice(atom_features, begin, size)

      # Apply hidden affine to relevant atoms and append
      rel_out = tf.matmul(rel_atoms, next(W)) + next(b)
      self_out = tf.matmul(self_atoms, next(W)) + next(b)
      out = rel_out + self_out

      new_rel_atoms_collection[deg - self.min_degree] = out

    # Determine the min_deg=0 case
    if self.min_degree == 0:
      deg = 0

      begin = tf.stack([deg_slice[deg - self.min_degree, 0], 0])
      size = tf.stack([deg_slice[deg - self.min_degree, 1], -1])
      self_atoms = tf.slice(atom_features, begin, size)

      # Only use the self layer
      out = tf.matmul(self_atoms, next(W)) + next(b)

      new_rel_atoms_collection[deg - self.min_degree] = out

    # Combine all atoms back into the list
    atom_features = tf.concat(axis=0, values=new_rel_atoms_collection)

    if self.activation_fn is not None:
      atom_features = self.activation_fn(atom_features)

    out_tensor = atom_features
    if set_tensors:
      self._record_variable_scope(self.name)
      self.out_tensor = out_tensor
    return out_tensor

  def sum_neigh(self, atoms, deg_adj_lists):
    """Store the summed atoms by degree"""
    deg_summed = self.max_degree * [None]

    # Tensorflow correctly processes empty lists when using concat
    for deg in range(1, self.max_degree + 1):
      gathered_atoms = tf.gather(atoms, deg_adj_lists[deg - 1])
      # Sum along neighbors as well as self, and store
      summed_atoms = tf.reduce_sum(gathered_atoms, 1)
      deg_summed[deg - 1] = summed_atoms

    return deg_summed

  def none_tensors(self):
    out_tensor, W_list, b_list = self.out_tensor, self.W_list, self.b_list
    self.out_tensor, self.W_list, self.b_list = None, None, None
    return out_tensor, W_list, b_list

  def set_tensors(self, tensors):
    self.out_tensor, self.W_list, self.b_list = tensors


class GraphPool(Layer):

  def __init__(self, min_degree=0, max_degree=10, **kwargs):
    self.min_degree = min_degree
    self.max_degree = max_degree
    super(GraphPool, self).__init__(**kwargs)

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    atom_features = inputs[0]
    deg_slice = inputs[1]
    deg_adj_lists = inputs[3:]

    # Perform the mol gather
    # atom_features = graph_pool(atom_features, deg_adj_lists, deg_slice,
    #                            self.max_degree, self.min_degree)

    deg_maxed = (self.max_degree + 1 - self.min_degree) * [None]

    # Tensorflow correctly processes empty lists when using concat

    for deg in range(1, self.max_degree + 1):
      # Get self atoms
      begin = tf.stack([deg_slice[deg - self.min_degree, 0], 0])
      size = tf.stack([deg_slice[deg - self.min_degree, 1], -1])
      self_atoms = tf.slice(atom_features, begin, size)

      # Expand dims
      self_atoms = tf.expand_dims(self_atoms, 1)

      # always deg-1 for deg_adj_lists
      gathered_atoms = tf.gather(atom_features, deg_adj_lists[deg - 1])
      gathered_atoms = tf.concat(axis=1, values=[self_atoms, gathered_atoms])

      maxed_atoms = tf.reduce_max(gathered_atoms, 1)
      deg_maxed[deg - self.min_degree] = maxed_atoms

    if self.min_degree == 0:
      begin = tf.stack([deg_slice[0, 0], 0])
      size = tf.stack([deg_slice[0, 1], -1])
      self_atoms = tf.slice(atom_features, begin, size)
      deg_maxed[0] = self_atoms

    out_tensor = tf.concat(axis=0, values=deg_maxed)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class GraphGather(Layer):

  def __init__(self, batch_size, activation_fn=None, **kwargs):
    self.batch_size = batch_size
    self.activation_fn = activation_fn
    super(GraphGather, self).__init__(**kwargs)

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)

    # x = [atom_features, deg_slice, membership, deg_adj_list placeholders...]
    atom_features = inputs[0]

    # Extract graph topology
    membership = inputs[2]

    # Perform the mol gather

    assert self.batch_size > 1, "graph_gather requires batches larger than 1"

    # Obtain the partitions for each of the molecules
    activated_par = tf.dynamic_partition(atom_features, membership,
                                         self.batch_size)

    # Sum over atoms for each molecule
    sparse_reps = [
        tf.reduce_mean(activated, 0, keep_dims=True)
        for activated in activated_par
    ]
    max_reps = [
        tf.reduce_max(activated, 0, keep_dims=True)
        for activated in activated_par
    ]

    # Get the final sparse representations
    sparse_reps = tf.concat(axis=0, values=sparse_reps)
    max_reps = tf.concat(axis=0, values=max_reps)
    mol_features = tf.concat(axis=1, values=[sparse_reps, max_reps])

    if self.activation_fn is not None:
      mol_features = self.activation_fn(mol_features)
    out_tensor = mol_features
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class LSTMStep(Layer):
  """Layer that performs a single step LSTM update.

  This layer performs a single step LSTM update. Note that it is *not*
  a full LSTM recurrent network. The LSTMStep layer is useful as a
  primitive for designing layers such as the AttnLSTMEmbedding or the
  IterRefLSTMEmbedding below.
  """

  def __init__(self,
               output_dim,
               input_dim,
               init_fn=initializations.glorot_uniform,
               inner_init_fn=initializations.orthogonal,
               activation_fn=activations.tanh,
               inner_activation_fn=activations.hard_sigmoid,
               **kwargs):
    """
    Parameters
    ----------
    output_dim: int
      Dimensionality of output vectors.
    input_dim: int
      Dimensionality of input vectors.
    init_fn: object 
      TensorFlow initialization to use for W. 
    inner_init_fn: object 
      TensorFlow initialization to use for U. 
    activation_fn: object 
      TensorFlow activation to use for output. 
    inner_activation_fn: object 
      TensorFlow activation to use for inner steps. 
    """

    super(LSTMStep, self).__init__(**kwargs)

    self.init = init_fn
    self.inner_init = inner_init_fn
    self.output_dim = output_dim

    # No other forget biases supported right now.
    self.activation = activation_fn
    self.inner_activation = inner_activation_fn
    self.input_dim = input_dim

  def get_initial_states(self, input_shape):
    return [model_ops.zeros(input_shape), model_ops.zeros(input_shape)]

  def build(self):
    """Constructs learnable weights for this layer."""
    init = self.init
    inner_init = self.inner_init
    self.W = init((self.input_dim, 4 * self.output_dim))
    self.U = inner_init((self.output_dim, 4 * self.output_dim))

    self.b = tf.Variable(
        np.hstack((np.zeros(self.output_dim), np.ones(self.output_dim),
                   np.zeros(self.output_dim), np.zeros(self.output_dim))),
        dtype=tf.float32)
    self.trainable_weights = [self.W, self.U, self.b]

  def none_tensors(self):
    """Zeros out stored tensors for pickling."""
    W, U, b, out_tensor = self.W, self.U, self.b, self.out_tensor
    h, c = self.h, self.c
    trainable_weights = self.trainable_weights
    self.W, self.U, self.b, self.out_tensor = None, None, None, None
    self.h, self.c = None, None
    self.trainable_weights = []
    return W, U, b, h, c, out_tensor, trainable_weights

  def set_tensors(self, tensor):
    """Sets all stored tensors."""
    (self.W, self.U, self.b, self.h, self.c, self.out_tensor,
     self.trainable_weights) = tensor

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    """Execute this layer on input tensors.

    Parameters
    ----------
    in_layers: list
      List of three tensors (x, h_tm1, c_tm1). h_tm1 means "h, t-1".

    Returns
    -------
    list
      Returns h, [h + c] 
    """
    activation = self.activation
    inner_activation = self.inner_activation

    self.build()
    inputs = self._get_input_tensors(in_layers)
    x, h_tm1, c_tm1 = inputs

    # Taken from Keras code [citation needed]
    z = model_ops.dot(x, self.W) + model_ops.dot(h_tm1, self.U) + self.b

    z0 = z[:, :self.output_dim]
    z1 = z[:, self.output_dim:2 * self.output_dim]
    z2 = z[:, 2 * self.output_dim:3 * self.output_dim]
    z3 = z[:, 3 * self.output_dim:]

    i = inner_activation(z0)
    f = inner_activation(z1)
    c = f * c_tm1 + i * activation(z2)
    o = inner_activation(z3)

    h = o * activation(c)

    if set_tensors:
      self.h = h
      self.c = c
      self.out_tensor = h
    return h, [h, c]


def _cosine_dist(x, y):
  """Computes the inner product (cosine distance) between two tensors.

  Parameters
  ----------
  x: tf.Tensor
    Input Tensor
  y: tf.Tensor
    Input Tensor 
  """
  denom = (
      model_ops.sqrt(model_ops.sum(tf.square(x)) * model_ops.sum(tf.square(y)))
      + model_ops.epsilon())
  return model_ops.dot(x, tf.transpose(y)) / denom


class AttnLSTMEmbedding(Layer):
  """Implements AttnLSTM as in matching networks paper.

  The AttnLSTM embedding adjusts two sets of vectors, the "test" and
  "support" sets. The "support" consists of a set of evidence vectors.
  Think of these as the small training set for low-data machine
  learning.  The "test" consists of the queries we wish to answer with
  the small amounts ofavailable data. The AttnLSTMEmbdding allows us to
  modify the embedding of the "test" set depending on the contents of
  the "support".  The AttnLSTMEmbedding is thus a type of learnable
  metric that allows a network to modify its internal notion of
  distance.

  References:
  Matching Networks for One Shot Learning
  https://arxiv.org/pdf/1606.04080v1.pdf

  Order Matters: Sequence to sequence for sets
  https://arxiv.org/abs/1511.06391
  """

  def __init__(self, n_test, n_support, n_feat, max_depth, **kwargs):
    """
    Parameters
    ----------
    n_support: int
      Size of support set.
    n_test: int
      Size of test set.
    n_feat: int
      Number of features per atom
    max_depth: int
      Number of "processing steps" used by sequence-to-sequence for sets model.
    """
    super(AttnLSTMEmbedding, self).__init__(**kwargs)

    self.max_depth = max_depth
    self.n_test = n_test
    self.n_support = n_support
    self.n_feat = n_feat

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    """Execute this layer on input tensors.

    Parameters
    ----------
    in_layers: list
      List of two tensors (X, Xp). X should be of shape (n_test,
      n_feat) and Xp should be of shape (n_support, n_feat) where
      n_test is the size of the test set, n_support that of the support
      set, and n_feat is the number of per-atom features.

    Returns
    -------
    list
      Returns two tensors of same shape as input. Namely the output
      shape will be [(n_test, n_feat), (n_support, n_feat)]
    """
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) != 2:
      raise ValueError("AttnLSTMEmbedding layer must have exactly two parents")
    # x is test set, xp is support set.
    x, xp = inputs

    ## Initializes trainable weights.
    n_feat = self.n_feat

    lstm = LSTMStep(n_feat, 2 * n_feat)
    self.q_init = model_ops.zeros([self.n_test, n_feat])
    self.r_init = model_ops.zeros([self.n_test, n_feat])
    self.states_init = lstm.get_initial_states([self.n_test, n_feat])

    self.trainable_weights = [self.q_init, self.r_init]

    ### Performs computations

    # Get initializations
    q = self.q_init
    states = self.states_init

    for d in range(self.max_depth):
      # Process using attention
      # Eqn (4), appendix A.1 of Matching Networks paper
      e = _cosine_dist(x + q, xp)
      a = tf.nn.softmax(e)
      r = model_ops.dot(a, xp)

      # Generate new attention states
      y = model_ops.concatenate([q, r], axis=1)
      q, states = lstm(y, *states)

    if set_tensors:
      self.out_tensor = xp
      self.xq = x + q
      self.xp = xp
    return [x + q, xp]

  def none_tensors(self):
    q_init, r_init, states_init = self.q_init, self.r_init, self.states_init
    xq, xp = self.xq, self.xp
    out_tensor = self.out_tensor
    trainable_weights = self.trainable_weights
    self.q_init, self.r_init, self.states_init = None, None, None
    self.xq, self.xp = None, None
    self.out_tensor = None
    self.trainable_weights = []
    return q_init, r_init, states_init, xq, xp, out_tensor, trainable_weights

  def set_tensors(self, tensor):
    (self.q_init, self.r_init, self.states_init, self.xq, self.xp,
     self.out_tensor, self.trainable_weights) = tensor


class IterRefLSTMEmbedding(Layer):
  """Implements the Iterative Refinement LSTM.

  Much like AttnLSTMEmbedding, the IterRefLSTMEmbedding is another type
  of learnable metric which adjusts "test" and "support." Recall that
  "support" is the small amount of data available in a low data machine
  learning problem, and that "test" is the query. The AttnLSTMEmbedding
  only modifies the "test" based on the contents of the support.
  However, the IterRefLSTM modifies both the "support" and "test" based
  on each other. This allows the learnable metric to be more malleable
  than that from AttnLSTMEmbeding.
  """

  def __init__(self, n_test, n_support, n_feat, max_depth, **kwargs):
    """
    Unlike the AttnLSTM model which only modifies the test vectors
    additively, this model allows for an additive update to be
    performed to both test and support using information from each
    other.

    Parameters
    ----------
    n_support: int
      Size of support set.
    n_test: int
      Size of test set.
    n_feat: int
      Number of input atom features
    max_depth: int
      Number of LSTM Embedding layers.
    """
    super(IterRefLSTMEmbedding, self).__init__(**kwargs)

    self.max_depth = max_depth
    self.n_test = n_test
    self.n_support = n_support
    self.n_feat = n_feat

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    """Execute this layer on input tensors.

    Parameters
    ----------
    in_layers: list
      List of two tensors (X, Xp). X should be of shape (n_test, n_feat) and
      Xp should be of shape (n_support, n_feat) where n_test is the size of
      the test set, n_support that of the support set, and n_feat is the number
      of per-atom features.

    Returns
    -------
    list
      Returns two tensors of same shape as input. Namely the output shape will
      be [(n_test, n_feat), (n_support, n_feat)]
    """
    n_feat = self.n_feat

    # Support set lstm
    support_lstm = LSTMStep(n_feat, 2 * n_feat)
    self.q_init = model_ops.zeros([self.n_support, n_feat])
    self.support_states_init = support_lstm.get_initial_states(
        [self.n_support, n_feat])

    # Test lstm
    test_lstm = LSTMStep(n_feat, 2 * n_feat)
    self.p_init = model_ops.zeros([self.n_test, n_feat])
    self.test_states_init = test_lstm.get_initial_states([self.n_test, n_feat])

    self.trainable_weights = []

    #self.build()
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) != 2:
      raise ValueError(
          "IterRefLSTMEmbedding layer must have exactly two parents")
    x, xp = inputs

    # Get initializations
    p = self.p_init
    q = self.q_init
    # Rename support
    z = xp
    states = self.support_states_init
    x_states = self.test_states_init

    for d in range(self.max_depth):
      # Process support xp using attention
      e = _cosine_dist(z + q, xp)
      a = tf.nn.softmax(e)
      # Get linear combination of support set
      r = model_ops.dot(a, xp)

      # Process test x using attention
      x_e = _cosine_dist(x + p, z)
      x_a = tf.nn.softmax(x_e)
      s = model_ops.dot(x_a, z)

      # Generate new support attention states
      qr = model_ops.concatenate([q, r], axis=1)
      q, states = support_lstm(qr, *states)

      # Generate new test attention states
      ps = model_ops.concatenate([p, s], axis=1)
      p, x_states = test_lstm(ps, *x_states)

      # Redefine
      z = r

    if set_tensors:
      self.xp = x + p
      self.xpq = xp + q
      self.out_tensor = self.xp

    return [x + p, xp + q]

  def none_tensors(self):
    p_init, q_init = self.p_init, self.q_init,
    support_states_init, test_states_init = (self.support_states_init,
                                             self.test_states_init)
    xp, xpq = self.xp, self.xpq
    out_tensor = self.out_tensor
    trainable_weights = self.trainable_weights
    (self.p_init, self.q_init, self.support_states_init,
     self.test_states_init) = (None, None, None, None)
    self.xp, self.xpq = None, None
    self.out_tensor = None
    self.trainable_weights = []
    return (p_init, q_init, support_states_init, test_states_init, xp, xpq,
            out_tensor, trainable_weights)

  def set_tensors(self, tensor):
    (self.p_init, self.q_init, self.support_states_init, self.test_states_init,
     self.xp, self.xpq, self.out_tensor, self.trainable_weights) = tensor


class BatchNorm(Layer):

  def __init__(self, **kwargs):
    super(BatchNorm, self).__init__(**kwargs)
    try:
      parent_shape = self.in_layers[0].shape
      self._shape = tuple(self.in_layers[0].shape)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    parent_tensor = inputs[0]
    out_tensor = tf.layers.batch_normalization(parent_tensor)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class BatchNormalization(Layer):

  def __init__(self,
               epsilon=1e-5,
               axis=-1,
               momentum=0.99,
               beta_init='zero',
               gamma_init='one',
               **kwargs):
    self.beta_init = initializations.get(beta_init)
    self.gamma_init = initializations.get(gamma_init)
    self.epsilon = epsilon
    self.axis = axis
    self.momentum = momentum
    super(BatchNormalization, self).__init__(**kwargs)

  def add_weight(self, shape, initializer, name=None):
    initializer = initializations.get(initializer)
    weight = initializer(shape, name=name)
    return weight

  def build(self, input_shape):
    shape = (input_shape[self.axis],)
    self.gamma = self.add_weight(
        shape, initializer=self.gamma_init, name='{}_gamma'.format(self.name))
    self.beta = self.add_weight(
        shape, initializer=self.beta_init, name='{}_beta'.format(self.name))

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    x = inputs[0]
    input_shape = model_ops.int_shape(x)
    self.build(input_shape)
    m = model_ops.mean(x, axis=-1, keepdims=True)
    std = model_ops.sqrt(
        model_ops.var(x, axis=-1, keepdims=True) + self.epsilon)
    x_normed = (x - m) / (std + self.epsilon)
    x_normed = self.gamma * x_normed + self.beta
    out_tensor = x_normed
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class WeightedError(Layer):

  def __init__(self, **kwargs):
    super(WeightedError, self).__init__(**kwargs)
    self._shape = tuple()

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers, True)
    entropy, weights = inputs[0], inputs[1]
    out_tensor = tf.reduce_sum(entropy * weights)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class VinaFreeEnergy(Layer):
  """Computes free-energy as defined by Autodock Vina.

  TODO(rbharath): Make this layer support batching.
  """

  def __init__(self,
               N_atoms,
               M_nbrs,
               ndim,
               nbr_cutoff,
               start,
               stop,
               stddev=.3,
               Nrot=1,
               **kwargs):
    self.stddev = stddev
    # Number of rotatable bonds
    # TODO(rbharath): Vina actually sets this per-molecule. See if makes
    # a difference.
    self.Nrot = Nrot
    self.N_atoms = N_atoms
    self.M_nbrs = M_nbrs
    self.ndim = ndim
    self.nbr_cutoff = nbr_cutoff
    self.start = start
    self.stop = stop
    super(VinaFreeEnergy, self).__init__(**kwargs)

  def cutoff(self, d, x):
    out_tensor = tf.where(d < 8, x, tf.zeros_like(x))
    return out_tensor

  def nonlinearity(self, c):
    """Computes non-linearity used in Vina."""
    w = tf.Variable(tf.random_normal((1,), stddev=self.stddev))
    out_tensor = c / (1 + w * self.Nrot)
    return w, out_tensor

  def repulsion(self, d):
    """Computes Autodock Vina's repulsion interaction term."""
    out_tensor = tf.where(d < 0, d**2, tf.zeros_like(d))
    return out_tensor

  def hydrophobic(self, d):
    """Computes Autodock Vina's hydrophobic interaction term."""
    out_tensor = tf.where(d < 0.5,
                          tf.ones_like(d),
                          tf.where(d < 1.5, 1.5 - d, tf.zeros_like(d)))
    return out_tensor

  def hydrogen_bond(self, d):
    """Computes Autodock Vina's hydrogen bond interaction term."""
    out_tensor = tf.where(d < -0.7,
                          tf.ones_like(d),
                          tf.where(d < 0, (1.0 / 0.7) * (0 - d),
                                   tf.zeros_like(d)))
    return out_tensor

  def gaussian_first(self, d):
    """Computes Autodock Vina's first Gaussian interaction term."""
    out_tensor = tf.exp(-(d / 0.5)**2)
    return out_tensor

  def gaussian_second(self, d):
    """Computes Autodock Vina's second Gaussian interaction term."""
    out_tensor = tf.exp(-((d - 3) / 2)**2)
    return out_tensor

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    """
    Parameters
    ----------
    X: tf.Tensor of shape (N, d)
      Coordinates/features.
    Z: tf.Tensor of shape (N)
      Atomic numbers of neighbor atoms.

    Returns
    -------
    layer: tf.Tensor of shape (B)
      The free energy of each complex in batch
    """
    inputs = self._get_input_tensors(in_layers)
    X = inputs[0]
    Z = inputs[1]

    # TODO(rbharath): This layer shouldn't be neighbor-listing. Make
    # neighbors lists an argument instead of a part of this layer.
    nbr_list = NeighborList(self.N_atoms, self.M_nbrs, self.ndim,
                            self.nbr_cutoff, self.start, self.stop)(X)

    # Shape (N, M)
    dists = InteratomicL2Distances(self.N_atoms, self.M_nbrs,
                                   self.ndim)(X, nbr_list)

    repulsion = self.repulsion(dists)
    hydrophobic = self.hydrophobic(dists)
    hbond = self.hydrogen_bond(dists)
    gauss_1 = self.gaussian_first(dists)
    gauss_2 = self.gaussian_second(dists)

    # Shape (N, M)
    weighted_combo = WeightedLinearCombo()
    interactions = weighted_combo(repulsion, hydrophobic, hbond, gauss_1,
                                  gauss_2)

    # Shape (N, M)
    thresholded = self.cutoff(dists, interactions)

    weight, free_energies = self.nonlinearity(thresholded)
    free_energy = ReduceSum()(free_energies)

    out_tensor = free_energy
    if set_tensors:
      self._record_variable_scope(self.name)
      self.out_tensor = out_tensor
    return out_tensor


class WeightedLinearCombo(Layer):
  """Computes a weighted linear combination of input layers, with the weights defined by trainable variables."""

  def __init__(self, std=.3, **kwargs):
    self.std = std
    super(WeightedLinearCombo, self).__init__(**kwargs)
    try:
      self._shape = tuple(self.in_layers[0].shape)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers, True)
    weights = []
    out_tensor = None
    for in_tensor in inputs:
      w = tf.Variable(tf.random_normal([
          1,
      ], stddev=self.std))
      if out_tensor is None:
        out_tensor = w * in_tensor
      else:
        out_tensor += w * in_tensor
    if set_tensors:
      self._record_variable_scope(self.name)
      self.out_tensor = out_tensor
    return out_tensor


class NeighborList(Layer):
  """Computes a neighbor-list in Tensorflow.

  Neighbor-lists (also called Verlet Lists) are a tool for grouping atoms which
  are close to each other spatially

  TODO(rbharath): Make this layer support batching.
  """

  def __init__(self, N_atoms, M_nbrs, ndim, nbr_cutoff, start, stop, **kwargs):
    """
    Parameters
    ----------
    N_atoms: int
      Maximum number of atoms this layer will neighbor-list.
    M_nbrs: int
      Maximum number of spatial neighbors possible for atom.
    ndim: int
      Dimensionality of space atoms live in. (Typically 3D, but sometimes will
      want to use higher dimensional descriptors for atoms).
    nbr_cutoff: float
      Length in Angstroms (?) at which atom boxes are gridded.
    """
    self.N_atoms = N_atoms
    self.M_nbrs = M_nbrs
    self.ndim = ndim
    # Number of grid cells
    n_cells = int(((stop - start) / nbr_cutoff)**ndim)
    self.n_cells = n_cells
    self.nbr_cutoff = nbr_cutoff
    self.start = start
    self.stop = stop
    super(NeighborList, self).__init__(**kwargs)

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    """Creates tensors associated with neighbor-listing."""
    inputs = self._get_input_tensors(in_layers)
    if len(inputs) != 1:
      raise ValueError("NeighborList can only have one input")
    parent = inputs[0]
    if len(parent.get_shape()) != 2:
      # TODO(rbharath): Support batching
      raise ValueError("Parent tensor must be (num_atoms, ndum)")
    coords = parent
    out_tensor = self.compute_nbr_list(coords)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor

  def compute_nbr_list(self, coords):
    """Get closest neighbors for atoms.

    Needs to handle padding for atoms with no neighbors.

    Parameters
    ----------
    coords: tf.Tensor
      Shape (N_atoms, ndim)

    Returns
    -------
    nbr_list: tf.Tensor
      Shape (N_atoms, M_nbrs) of atom indices
    """
    # Shape (n_cells, ndim)
    cells = self.get_cells()

    # List of length N_atoms, each element of different length uniques_i
    nbrs = self.get_atoms_in_nbrs(coords, cells)
    padding = tf.fill((self.M_nbrs,), -1)
    padded_nbrs = [tf.concat([unique_nbrs, padding], 0) for unique_nbrs in nbrs]

    # List of length N_atoms, each element of different length uniques_i
    # List of length N_atoms, each a tensor of shape
    # (uniques_i, ndim)
    nbr_coords = [tf.gather(coords, atom_nbrs) for atom_nbrs in nbrs]

    # Add phantom atoms that exist far outside the box
    coord_padding = tf.to_float(
        tf.fill((self.M_nbrs, self.ndim), 2 * self.stop))
    padded_nbr_coords = [
        tf.concat([nbr_coord, coord_padding], 0) for nbr_coord in nbr_coords
    ]

    # List of length N_atoms, each of shape (1, ndim)
    atom_coords = tf.split(coords, self.N_atoms)
    # TODO(rbharath): How does distance need to be modified here to
    # account for periodic boundary conditions?
    # List of length N_atoms each of shape (M_nbrs)
    padded_dists = [
        tf.reduce_sum((atom_coord - padded_nbr_coord)**2, axis=1)
        for (atom_coord,
             padded_nbr_coord) in zip(atom_coords, padded_nbr_coords)
    ]

    padded_closest_nbrs = [
        tf.nn.top_k(-padded_dist, k=self.M_nbrs)[1]
        for padded_dist in padded_dists
    ]

    # N_atoms elts of size (M_nbrs,) each
    padded_neighbor_list = [
        tf.gather(padded_atom_nbrs, padded_closest_nbr)
        for (padded_atom_nbrs,
             padded_closest_nbr) in zip(padded_nbrs, padded_closest_nbrs)
    ]

    neighbor_list = tf.stack(padded_neighbor_list)

    return neighbor_list

  def get_atoms_in_nbrs(self, coords, cells):
    """Get the atoms in neighboring cells for each cells.

    Returns
    -------
    atoms_in_nbrs = (N_atoms, n_nbr_cells, M_nbrs)
    """
    # Shape (N_atoms, 1)
    cells_for_atoms = self.get_cells_for_atoms(coords, cells)

    # Find M_nbrs atoms closest to each cell
    # Shape (n_cells, M_nbrs)
    closest_atoms = self.get_closest_atoms(coords, cells)

    # Associate each cell with its neighbor cells. Assumes periodic boundary
    # conditions, so does wrapround. O(constant)
    # Shape (n_cells, n_nbr_cells)
    neighbor_cells = self.get_neighbor_cells(cells)

    # Shape (N_atoms, n_nbr_cells)
    neighbor_cells = tf.squeeze(tf.gather(neighbor_cells, cells_for_atoms))

    # Shape (N_atoms, n_nbr_cells, M_nbrs)
    atoms_in_nbrs = tf.gather(closest_atoms, neighbor_cells)

    # Shape (N_atoms, n_nbr_cells*M_nbrs)
    atoms_in_nbrs = tf.reshape(atoms_in_nbrs, [self.N_atoms, -1])

    # List of length N_atoms, each element length uniques_i
    nbrs_per_atom = tf.split(atoms_in_nbrs, self.N_atoms)
    uniques = [
        tf.unique(tf.squeeze(atom_nbrs))[0] for atom_nbrs in nbrs_per_atom
    ]

    # TODO(rbharath): FRAGILE! Uses fact that identity seems to be the first
    # element removed to remove self from list of neighbors. Need to verify
    # this holds more broadly or come up with robust alternative.
    uniques = [unique[1:] for unique in uniques]

    return uniques

  def get_closest_atoms(self, coords, cells):
    """For each cell, find M_nbrs closest atoms.

    Let N_atoms be the number of atoms.

    Parameters
    ----------
    coords: tf.Tensor
      (N_atoms, ndim) shape.
    cells: tf.Tensor
      (n_cells, ndim) shape.

    Returns
    -------
    closest_inds: tf.Tensor
      Of shape (n_cells, M_nbrs)
    """
    N_atoms, n_cells, ndim, M_nbrs = (self.N_atoms, self.n_cells, self.ndim,
                                      self.M_nbrs)
    # Tile both cells and coords to form arrays of size (N_atoms*n_cells, ndim)
    tiled_cells = tf.reshape(
        tf.tile(cells, (1, N_atoms)), (N_atoms * n_cells, ndim))

    # Shape (N_atoms*n_cells, ndim) after tile
    tiled_coords = tf.tile(coords, (n_cells, 1))

    # Shape (N_atoms*n_cells)
    coords_vec = tf.reduce_sum((tiled_coords - tiled_cells)**2, axis=1)
    # Shape (n_cells, N_atoms)
    coords_norm = tf.reshape(coords_vec, (n_cells, N_atoms))

    # Find k atoms closest to this cell. Notice negative sign since
    # tf.nn.top_k returns *largest* not smallest.
    # Tensor of shape (n_cells, M_nbrs)
    closest_inds = tf.nn.top_k(-coords_norm, k=M_nbrs)[1]

    return closest_inds

  def get_cells_for_atoms(self, coords, cells):
    """Compute the cells each atom belongs to.

    Parameters
    ----------
    coords: tf.Tensor
      Shape (N_atoms, ndim)
    cells: tf.Tensor
      (n_cells, ndim) shape.
    Returns
    -------
    cells_for_atoms: tf.Tensor
      Shape (N_atoms, 1)
    """
    N_atoms, n_cells, ndim = self.N_atoms, self.n_cells, self.ndim
    n_cells = int(n_cells)
    # Tile both cells and coords to form arrays of size (N_atoms*n_cells, ndim)
    tiled_cells = tf.tile(cells, (N_atoms, 1))

    # Shape (N_atoms*n_cells, 1) after tile
    tiled_coords = tf.reshape(
        tf.tile(coords, (1, n_cells)), (n_cells * N_atoms, ndim))
    coords_vec = tf.reduce_sum((tiled_coords - tiled_cells)**2, axis=1)
    coords_norm = tf.reshape(coords_vec, (N_atoms, n_cells))

    closest_inds = tf.nn.top_k(-coords_norm, k=1)[1]
    return closest_inds

  def _get_num_nbrs(self):
    """Get number of neighbors in current dimensionality space."""
    ndim = self.ndim
    if ndim == 1:
      n_nbr_cells = 3
    elif ndim == 2:
      # 9 neighbors in 2-space
      n_nbr_cells = 9
    # TODO(rbharath): Shoddy handling of higher dimensions...
    elif ndim >= 3:
      # Number of cells for cube in 3-space is
      n_nbr_cells = 27  # (26 faces on Rubik's cube for example)
    return n_nbr_cells

  def get_neighbor_cells(self, cells):
    """Compute neighbors of cells in grid.

    # TODO(rbharath): Do we need to handle periodic boundary conditions
    properly here?
    # TODO(rbharath): This doesn't handle boundaries well. We hard-code
    # looking for n_nbr_cells neighbors, which isn't right for boundary cells in
    # the cube.

    Parameters
    ----------
    cells: tf.Tensor
      (n_cells, ndim) shape.
    Returns
    -------
    nbr_cells: tf.Tensor
      (n_cells, n_nbr_cells)
    """
    ndim, n_cells = self.ndim, self.n_cells
    n_nbr_cells = self._get_num_nbrs()
    # Tile cells to form arrays of size (n_cells*n_cells, ndim)
    # Two tilings (a, b, c, a, b, c, ...) vs. (a, a, a, b, b, b, etc.)
    # Tile (a, a, a, b, b, b, etc.)
    tiled_centers = tf.reshape(
        tf.tile(cells, (1, n_cells)), (n_cells * n_cells, ndim))
    # Tile (a, b, c, a, b, c, ...)
    tiled_cells = tf.tile(cells, (n_cells, 1))

    coords_vec = tf.reduce_sum((tiled_centers - tiled_cells)**2, axis=1)
    coords_norm = tf.reshape(coords_vec, (n_cells, n_cells))
    closest_inds = tf.nn.top_k(-coords_norm, k=n_nbr_cells)[1]

    return closest_inds

  def get_cells(self):
    """Returns the locations of all grid points in box.

    Suppose start is -10 Angstrom, stop is 10 Angstrom, nbr_cutoff is 1.
    Then would return a list of length 20^3 whose entries would be
    [(-10, -10, -10), (-10, -10, -9), ..., (9, 9, 9)]

    Returns
    -------
    cells: tf.Tensor
      (n_cells, ndim) shape.
    """
    start, stop, nbr_cutoff = self.start, self.stop, self.nbr_cutoff
    mesh_args = [tf.range(start, stop, nbr_cutoff) for _ in range(self.ndim)]
    return tf.to_float(
        tf.reshape(
            tf.transpose(tf.stack(tf.meshgrid(*mesh_args))), (self.n_cells,
                                                              self.ndim)))


class Dropout(Layer):

  def __init__(self, dropout_prob, **kwargs):
    self.dropout_prob = dropout_prob
    super(Dropout, self).__init__(**kwargs)
    try:
      self._shape = tuple(self.in_layers[0].shape)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    parent_tensor = inputs[0]
    keep_prob = 1.0 - self.dropout_prob * kwargs['training']
    out_tensor = tf.nn.dropout(parent_tensor, keep_prob)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class WeightDecay(Layer):
  """Apply a weight decay penalty.

  The input should be the loss value.  This layer adds a weight decay penalty to it
  and outputs the sum.
  """

  def __init__(self, penalty, penalty_type, **kwargs):
    """Create a weight decay penalty layer.

    Parameters
    ----------
    penalty: float
      magnitude of the penalty term
    penalty_type: str
      type of penalty to compute, either 'l1' or 'l2'
    """
    self.penalty = penalty
    self.penalty_type = penalty_type
    super(WeightDecay, self).__init__(**kwargs)
    try:
      self._shape = tuple(self.in_layers[0].shape)
    except:
      pass

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    inputs = self._get_input_tensors(in_layers)
    parent_tensor = inputs[0]
    out_tensor = parent_tensor + model_ops.weight_decay(self.penalty_type,
                                                        self.penalty)
    if set_tensors:
      self.out_tensor = out_tensor
    return out_tensor


class AtomicConvolution(Layer):

  def __init__(self,
               atom_types=None,
               radial_params=list(),
               boxsize=None,
               **kwargs):
    """Atomic convoluation layer

    N = max_num_atoms, M = max_num_neighbors, B = batch_size, d = num_features
    l = num_radial_filters * num_atom_types

    Parameters
    ----------

    atom_types: list or None
      Of length a, where a is number of atom types for filtering.
    radial_params: list
      Of length l, where l is number of radial filters learned.
    boxsize: float or None
      Simulation box length [Angstrom].

    """
    self.boxsize = boxsize
    self.radial_params = radial_params
    self.atom_types = atom_types
    super(AtomicConvolution, self).__init__(**kwargs)

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    """
    Parameters
    ----------
    X: tf.Tensor of shape (B, N, d)
      Coordinates/features.
    Nbrs: tf.Tensor of shape (B, N, M)
      Neighbor list.
    Nbrs_Z: tf.Tensor of shape (B, N, M)
      Atomic numbers of neighbor atoms.

    Returns
    -------
    layer: tf.Tensor of shape (B, N, l)
      A new tensor representing the output of the atomic conv layer
    """
    inputs = self._get_input_tensors(in_layers)
    X = inputs[0]
    Nbrs = tf.to_int32(inputs[1])
    Nbrs_Z = inputs[2]

    # N: Maximum number of atoms
    # M: Maximum number of neighbors
    # d: Number of coordinates/features/filters
    # B: Batch Size
    N = X.get_shape()[-2].value
    d = X.get_shape()[-1].value
    M = Nbrs.get_shape()[-1].value
    B = X.get_shape()[0].value

    D = self.distance_tensor(X, Nbrs, self.boxsize, B, N, M, d)
    R = self.distance_matrix(D)
    sym = []
    rsf_zeros = tf.zeros((B, N, M))
    for param in self.radial_params:

      # We apply the radial pooling filter before atom type conv
      # to reduce computation
      param_variables, rsf = self.radial_symmetry_function(R, *param)

      if not self.atom_types:
        cond = tf.not_equal(Nbrs_Z, 0.0)
        sym.append(tf.reduce_sum(tf.where(cond, rsf, rsf_zeros), 2))
      else:
        for j in range(len(self.atom_types)):
          cond = tf.equal(Nbrs_Z, self.atom_types[j])
          sym.append(tf.reduce_sum(tf.where(cond, rsf, rsf_zeros), 2))

    layer = tf.stack(sym)
    layer = tf.transpose(layer, [1, 2, 0])  # (l, B, N) -> (B, N, l)
    m, v = tf.nn.moments(layer, axes=[0])
    out_tensor = tf.nn.batch_normalization(layer, m, v, None, None, 1e-3)
    if set_tensors:
      self._record_variable_scope(self.name)
      self.out_tensor = out_tensor
    return out_tensor

  def radial_symmetry_function(self, R, rc, rs, e):
    """Calculates radial symmetry function.

    B = batch_size, N = max_num_atoms, M = max_num_neighbors, d = num_filters

    Parameters
    ----------
    R: tf.Tensor of shape (B, N, M)
      Distance matrix.
    rc: float
      Interaction cutoff [Angstrom].
    rs: float
      Gaussian distance matrix mean.
    e: float
      Gaussian distance matrix width.

    Returns
    -------
    retval: tf.Tensor of shape (B, N, M)
      Radial symmetry function (before summation)

    """

    with tf.name_scope(None, "NbrRadialSymmetryFunction", [rc, rs, e]):
      rc = tf.Variable(rc)
      rs = tf.Variable(rs)
      e = tf.Variable(e)
      K = self.gaussian_distance_matrix(R, rs, e)
      FC = self.radial_cutoff(R, rc)
    return [rc, rs, e], tf.multiply(K, FC)

  def radial_cutoff(self, R, rc):
    """Calculates radial cutoff matrix.

    B = batch_size, N = max_num_atoms, M = max_num_neighbors

    Parameters
    ----------
      R [B, N, M]: tf.Tensor
        Distance matrix.
      rc: tf.Variable
        Interaction cutoff [Angstrom].

    Returns
    -------
      FC [B, N, M]: tf.Tensor
        Radial cutoff matrix.

    """

    T = 0.5 * (tf.cos(np.pi * R / (rc)) + 1)
    E = tf.zeros_like(T)
    cond = tf.less_equal(R, rc)
    FC = tf.where(cond, T, E)
    return FC

  def gaussian_distance_matrix(self, R, rs, e):
    """Calculates gaussian distance matrix.

    B = batch_size, N = max_num_atoms, M = max_num_neighbors

    Parameters
    ----------
      R [B, N, M]: tf.Tensor
        Distance matrix.
      rs: tf.Variable
        Gaussian distance matrix mean.
      e: tf.Variable
        Gaussian distance matrix width (e = .5/std**2).

    Returns
    -------
      retval [B, N, M]: tf.Tensor
        Gaussian distance matrix.

    """

    return tf.exp(-e * (R - rs)**2)

  def distance_tensor(self, X, Nbrs, boxsize, B, N, M, d):
    """Calculates distance tensor for batch of molecules.

    B = batch_size, N = max_num_atoms, M = max_num_neighbors, d = num_features

    Parameters
    ----------
    X: tf.Tensor of shape (B, N, d)
      Coordinates/features tensor.
    Nbrs: tf.Tensor of shape (B, N, M)
      Neighbor list tensor.
    boxsize: float or None
      Simulation box length [Angstrom].

    Returns
    -------
    D: tf.Tensor of shape (B, N, M, d)
      Coordinates/features distance tensor.

    """
    atom_tensors = tf.unstack(X, axis=1)
    nbr_tensors = tf.unstack(Nbrs, axis=1)
    D = []
    if boxsize is not None:
      for atom, atom_tensor in enumerate(atom_tensors):
        nbrs = self.gather_neighbors(X, nbr_tensors[atom], B, N, M, d)
        nbrs_tensors = tf.unstack(nbrs, axis=1)
        for nbr, nbr_tensor in enumerate(nbrs_tensors):
          _D = tf.subtract(nbr_tensor, atom_tensor)
          _D = tf.subtract(_D, boxsize * tf.round(tf.div(_D, boxsize)))
          D.append(_D)
    else:
      for atom, atom_tensor in enumerate(atom_tensors):
        nbrs = self.gather_neighbors(X, nbr_tensors[atom], B, N, M, d)
        nbrs_tensors = tf.unstack(nbrs, axis=1)
        for nbr, nbr_tensor in enumerate(nbrs_tensors):
          _D = tf.subtract(nbr_tensor, atom_tensor)
          D.append(_D)
    D = tf.stack(D)
    D = tf.transpose(D, perm=[1, 0, 2])
    D = tf.reshape(D, [B, N, M, d])
    return D

  def gather_neighbors(self, X, nbr_indices, B, N, M, d):
    """Gathers the neighbor subsets of the atoms in X.

    B = batch_size, N = max_num_atoms, M = max_num_neighbors, d = num_features

    Parameters
    ----------
    X: tf.Tensor of shape (B, N, d)
      Coordinates/features tensor.
    atom_indices: tf.Tensor of shape (B, M)
      Neighbor list for single atom.

    Returns
    -------
    neighbors: tf.Tensor of shape (B, M, d)
      Neighbor coordinates/features tensor for single atom.

    """

    example_tensors = tf.unstack(X, axis=0)
    example_nbrs = tf.unstack(nbr_indices, axis=0)
    all_nbr_coords = []
    for example, (example_tensor,
                  example_nbr) in enumerate(zip(example_tensors, example_nbrs)):
      nbr_coords = tf.gather(example_tensor, example_nbr)
      all_nbr_coords.append(nbr_coords)
    neighbors = tf.stack(all_nbr_coords)
    return neighbors

  def distance_matrix(self, D):
    """Calcuates the distance matrix from the distance tensor

    B = batch_size, N = max_num_atoms, M = max_num_neighbors, d = num_features

    Parameters
    ----------
    D: tf.Tensor of shape (B, N, M, d)
      Distance tensor.

    Returns
    -------
    R: tf.Tensor of shape (B, N, M)
       Distance matrix.

    """

    R = tf.reduce_sum(tf.multiply(D, D), 3)
    R = tf.sqrt(R)
    return R

class AtomtypeConvolution(Layer):

  def __init__(self, atom_types=[], **kwargs):
    """Filter tensor by atom types."""
    self.atom_types = [int(at) for at in atom_types]
    super(AtomtypeConvolution, self).__init__(**kwargs)

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):

    inputs = self._get_input_tensors(in_layers)
    X = inputs[0]
    xdim = int(X.shape[-1])
    Nbrs_Z = tf.tile(tf.expand_dims(inputs[1], -1), [1,1,1,xdim])
    X_zeros = tf.zeros_like(X)

    out_tensors = []
    for at in self.atom_types:
      cond = tf.equal(Nbrs_Z, at)
      out_tensors.append(tf.where(cond, X, X_zeros))

    out_tensor = tf.concat(out_tensors, axis=-1)
    if set_tensors:
      self._record_variable_scope(self.name)
      self.out_tensor = out_tensor
    return out_tensor

class NbrDistanceMatrix(Layer):

  def __init__(self, boxsize=None, **kwargs):
    """Neighbor-listed Distance Matrix layer
    """
    self.boxsize = boxsize
    super(NbrDistanceMatrix, self).__init__(**kwargs)

  def create_tensor(self, in_layers=None, set_tensors=True, **kwargs):
    """
    Parameters
    ----------
    X: tf.Tensor of shape (B, N, d)
      Coordinates/features.
    Nbrs: tf.Tensor of shape (B, N, M)
      Neighbor list.

    Returns
    -------
    layer: tf.Tensor of shape (B, N, M)
      A new tensor representing the distance matrix
    """
    inputs = self._get_input_tensors(in_layers)
    X = inputs[0]
    Nbrs = tf.to_int32(inputs[1])

    # N: Maximum number of atoms
    # M: Maximum number of neighbors
    # d: Number of coordinates/features/filters
    # B: Batch Size
    N = X.get_shape()[-2].value
    d = X.get_shape()[-1].value
    M = Nbrs.get_shape()[-1].value
    B = X.get_shape()[0].value

    D = self.distance_tensor(X, Nbrs, self.boxsize, B, N, M, d)
    D_zeros = tf.zeros_like(D)
    R = self.distance_matrix(D)
    R_zeros = tf.zeros_like(R)
    R_tensor = tf.expand_dims(tf.where(tf.greater(R, R_zeros), tf.div(1., R), R_zeros), -1)
    D_tensor = tf.where(tf.greater(D, D_zeros), tf.div(D, tf.tile(tf.expand_dims(R, -1), [1, 1, 1, d])), D_zeros)
    out_tensor = tf.concat([R_tensor, D_tensor], axis=-1)

    if set_tensors:
      self._record_variable_scope(self.name)
      self.out_tensor = out_tensor
    return out_tensor

  def distance_tensor(self, X, Nbrs, boxsize, B, N, M, d):
    """Calculates distance tensor for batch of molecules.

    B = batch_size, N = max_num_atoms, M = max_num_neighbors, d = num_features

    Parameters
    ----------
    X: tf.Tensor of shape (B, N, d)
      Coordinates/features tensor.
    Nbrs: tf.Tensor of shape (B, N, M)
      Neighbor list tensor.
    boxsize: float or None
      Simulation box length [Angstrom].

    Returns
    -------
    D: tf.Tensor of shape (B, N, M, d)
      Coordinates/features distance tensor.

    """
    atom_tensors = tf.unstack(X, axis=1)
    nbr_tensors = tf.unstack(Nbrs, axis=1)
    D = []
    if boxsize is not None:
      for atom, atom_tensor in enumerate(atom_tensors):
        nbrs = self.gather_neighbors(X, nbr_tensors[atom], B, N, M, d)
        nbrs_tensors = tf.unstack(nbrs, axis=1)
        for nbr, nbr_tensor in enumerate(nbrs_tensors):
          _D = tf.subtract(nbr_tensor, atom_tensor)
          _D = tf.subtract(_D, boxsize * tf.round(tf.div(_D, boxsize)))
          D.append(_D)
    else:
      for atom, atom_tensor in enumerate(atom_tensors):
        nbrs = self.gather_neighbors(X, nbr_tensors[atom], B, N, M, d)
        nbrs_tensors = tf.unstack(nbrs, axis=1)
        for nbr, nbr_tensor in enumerate(nbrs_tensors):
          _D = tf.subtract(nbr_tensor, atom_tensor)
          D.append(_D)
    D = tf.stack(D)
    D = tf.transpose(D, perm=[1, 0, 2])
    D = tf.reshape(D, [B, N, M, d])
    return D

  def gather_neighbors(self, X, nbr_indices, B, N, M, d):
    """Gathers the neighbor subsets of the atoms in X.

    B = batch_size, N = max_num_atoms, M = max_num_neighbors, d = num_features

    Parameters
    ----------
    X: tf.Tensor of shape (B, N, d)
      Coordinates/features tensor.
    atom_indices: tf.Tensor of shape (B, M)
      Neighbor list for single atom.

    Returns
    -------
    neighbors: tf.Tensor of shape (B, M, d)
      Neighbor coordinates/features tensor for single atom.

    """

    example_tensors = tf.unstack(X, axis=0)
    example_nbrs = tf.unstack(nbr_indices, axis=0)
    all_nbr_coords = []
    for example, (example_tensor,
                  example_nbr) in enumerate(zip(example_tensors, example_nbrs)):
      nbr_coords = tf.gather(example_tensor, example_nbr)
      all_nbr_coords.append(nbr_coords)
    neighbors = tf.stack(all_nbr_coords)
    return neighbors

  def distance_matrix(self, D):
    """Calcuates the distance matrix from the distance tensor

    B = batch_size, N = max_num_atoms, M = max_num_neighbors, d = num_features

    Parameters
    ----------
    D: tf.Tensor of shape (B, N, M, d)
      Distance tensor.

    Returns
    -------
    R: tf.Tensor of shape (B, N, M)
       Distance matrix.

    """

    R = tf.reduce_sum(tf.multiply(D, D), 3)
    R = tf.sqrt(R)
    return R

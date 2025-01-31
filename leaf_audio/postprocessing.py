# coding=utf-8
# Copyright 2021 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Lint as: python3
"""Initializer classes for each layer of the learnable frontend."""

from typing import Union

import gin
import tensorflow.compat.v2 as tf


@gin.configurable
class ExponentialMovingAverage(tf.keras.layers.Layer):
  """Computes of an exponential moving average of an sequential input."""

  def __init__(
      self,
      coeff_init: Union[float, tf.Tensor],
      per_channel: bool = False, trainable: bool = False):
    """Initializes the ExponentialMovingAverage.

    Args:
      coeff_init: the value of the initial coeff.
      per_channel: whether the smoothing should be different per channel.
      trainable: whether the smoothing should be trained or not.
    """
    super().__init__(name='EMA')
    self._coeff_init = coeff_init
    self._per_channel = per_channel
    self._trainable = trainable

  def build(self, input_shape):
    num_channels = input_shape[-1]
    self._weights = self.add_weight(
        name='smooth',
        shape=(num_channels,) if self._per_channel else (1,),
        initializer=tf.keras.initializers.Constant(self._coeff_init),
        trainable=self._trainable)

  def call(self, inputs: tf.Tensor, initial_state: tf.Tensor):
    """Inputs is of shape [batch, seq_length, num_filters]."""
    w = tf.clip_by_value(self._weights, clip_value_min=0.0, clip_value_max=1.0)
    result = tf.scan(lambda a, x: w * x + (1.0 - w) * a,
                     tf.transpose(inputs, (1, 0, 2)),
                     initializer=initial_state)
    return tf.transpose(result, (1, 0, 2))


@gin.configurable
class PCENLayer(tf.keras.layers.Layer):
  """Per-Channel Energy Normalization.

  This applies a fixed or learnable normalization by an exponential moving
  average smoother, and a compression.
  See https://arxiv.org/abs/1607.05666 for more details.
  """

  def __init__(self,
               alpha: float = 0.96,
               smooth_coef: float = 0.04,
               delta: float = 2.0,
               root: float = 2.0,
               floor: float = 1e-6,
               trainable: bool = False,
               learn_smooth_coef: bool = False,
               per_channel_smooth_coef: bool = False,
               name='PCEN'):
    """PCEN constructor.

    Args:
      alpha: float, exponent of EMA smoother
      smooth_coef: float, smoothing coefficient of EMA
      delta: float, bias added before compression
      root: float, one over exponent applied for compression (r in the paper)
      floor: float, offset added to EMA smoother
      trainable: bool, False means fixed_pcen, True is trainable_pcen
      learn_smooth_coef: bool, True means we also learn the smoothing
        coefficient
      per_channel_smooth_coef: bool, True means each channel has its own smooth
        coefficient
      name: str, name of the layer
    """
    super().__init__(name=name)
    self._alpha_init = alpha
    self._delta_init = delta
    self._root_init = root
    self._smooth_coef = smooth_coef
    self._floor = floor
    self._trainable = trainable
    self._learn_smooth_coef = learn_smooth_coef
    self._per_channel_smooth_coef = per_channel_smooth_coef

  def build(self, input_shape):
    num_channels = input_shape[-1]
    self.alpha = self.add_weight(
        name='alpha',
        shape=[num_channels],
        initializer=tf.keras.initializers.Constant(self._alpha_init),
        trainable=self._trainable)
    self.delta = self.add_weight(
        name='delta',
        shape=[num_channels],
        initializer=tf.keras.initializers.Constant(self._delta_init),
        trainable=self._trainable)
    self.root = self.add_weight(
        name='root',
        shape=[num_channels],
        initializer=tf.keras.initializers.Constant(self._root_init),
        trainable=self._trainable)
    if self._learn_smooth_coef:
      self.ema = ExponentialMovingAverage(
          coeff_init=self._smooth_coef,
          per_channel=self._per_channel_smooth_coef,
          trainable=True)
    else:
      self.ema = tf.keras.layers.SimpleRNN(
          units=num_channels,
          activation=None,
          use_bias=False,
          kernel_initializer=tf.keras.initializers.Identity(
              gain=self._smooth_coef),
          recurrent_initializer=tf.keras.initializers.Identity(
              gain=1. - self._smooth_coef),
          return_sequences=True,
          trainable=False)

  def call(self, inputs):
    alpha = tf.math.minimum(self.alpha, 1.0)
    root = tf.math.maximum(self.root, 1.0)
    ema_smoother = self.ema(inputs, initial_state=tf.gather(inputs, 0, axis=1))
    one_over_root = 1. / root
    output = ((inputs / (self._floor + ema_smoother)**alpha + self.delta)
              **one_over_root - self.delta**one_over_root)
    return output

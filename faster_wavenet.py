# -*- coding: utf-8 -*-
import math
import numpy as np
import chainer, os, collections, six
from chainer import cuda, Variable, optimizers, serializers, function, optimizer
from chainer.utils import type_check
from chainer import functions as F
from chainer import links as L
from wavenet import WaveNet

class FasterWaveNet(WaveNet):

	def forward_one_step(self, padded_x_batch_data, softmax=True, return_numpy=False):
		x_batch = Variable(padded_x_batch_data)
		if self.gpu_enabled:
			x_batch.to_gpu()
		causal_output = self.forward_causal_block(x_batch)
		residual_output, sum_skip_connections = self.forward_residual_block(causal_output)
		softmax_output = self.forward_softmax_block(sum_skip_connections, softmax=softmax)
		if return_numpy:
			if self.gpu_enabled:
				softmax_output.to_cpu()
			return softmax_output.data
		return softmax_output

	def forward_causal_block(self, x_batch):
		self.prev_causal_outputs = []
		input_batch = x_batch
		for layer in self.causal_conv_layers:
			output = layer(input_batch)
			self.prev_causal_outputs.append(output.data)
			input_batch = output
		return output

	def forward_residual_block(self, x_batch):
		self.prev_residual_outputs_out = []
		self.prev_residual_outputs_z = []
		sum_skip_connections = 0
		input_batch = x_batch
		for i, layer in enumerate(self.residual_conv_layers):
			output, z = layer(input_batch)
			self.prev_residual_outputs_out.append(output.data)
			self.prev_residual_outputs_z.append(z.data)
			sum_skip_connections += z
			input_batch = output

		return output, sum_skip_connections

	def forward_softmax_block(self, x_batch, softmax=True):
		batchsize = x_batch.data.shape[0]
		input_batch = x_batch
		for layer in self.softmax_conv_layers:
			input_batch = F.elu(input_batch)
			output = layer(input_batch)
			input_batch = output
		if softmax:
			output = F.softmax(output)
		return output

	# for faster generation
	def _forward_one_step(self, x_batch_data, softmax=True, return_numpy=False):
		if hasattr(self, "prev_causal_outputs") == False or self.prev_causal_outputs is None:
			return self.forward_one_step(x_batch_data, softmax=softmax, return_numpy=return_numpy)

		if self.gpu_enabled:
			x_batch_data = cuda.to_gpu(x_batch_data)
		causal_output = self._forward_causal_block(x_batch_data)
		residual_output, sum_skip_connections = self._forward_residual_block(causal_output)
		softmax_output = self._forward_softmax_block(sum_skip_connections, softmax=softmax)
		if return_numpy:
			if self.gpu_enabled:
				softmax_output.to_cpu()
			return softmax_output.data
		return softmax_output

	def _forward_causal_block(self, x_batch_data):
		input = x_batch_data
		xp = cuda.get_array_module(input)
		for i, layer in enumerate(self.causal_conv_layers):
			output = layer._forward(input)

			prev_output = self.prev_causal_outputs[i]
			prev_output = xp.roll(prev_output, -1, axis=3)
			prev_output[0, :, 0, -1] = output[0, :, 0, 0]
			output = prev_output

			self.prev_causal_outputs[i] = output
			
			input = output
		return output

	def _forward_residual_block(self, x_batch):
		sum_skip_connections = 0
		input = x_batch
		xp = cuda.get_array_module(input)
		for i, layer in enumerate(self.residual_conv_layers):
			output, z = layer._forward(input)

			prev_output = self.prev_residual_outputs_out[i]
			prev_output = xp.roll(prev_output, -1, axis=3)
			prev_output[0, :, 0, -1] = output[0, :, 0, 0]
			output = prev_output

			prev_z = self.prev_residual_outputs_z[i]
			prev_z = xp.roll(prev_z, -1, axis=3)
			prev_z[0, :, 0, -1] = z[0, :, 0, 0]
			z = prev_z

			self.prev_residual_outputs_out[i] = output
			self.prev_residual_outputs_z[i] = z

			sum_skip_connections += z
			input = output

		return output, sum_skip_connections

	def _forward_softmax_block(self, x_batch, softmax=True):
		input_batch = Variable(x_batch)
		for layer in self.softmax_conv_layers:
			input_batch = F.elu(input_batch)
			output = layer(input_batch)
			input_batch = output
		if softmax:
			output = F.softmax(output)
		return output
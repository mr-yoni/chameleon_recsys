#!/bin/bash
DATA_DIR="/home/user/Dropbox" && \
python3 -m acr.preprocessing.acr_preprocess_gcom \
	--input_articles_csv_path ${DATA_DIR}/Data/input_articles.tsv \
 	--input_word_embeddings_path ${DATA_DIR}/articles_word2vec/w2v_model \
 	--vocab_most_freq_words 50000 \
 	--output_word_vocab_embeddings_path ${DATA_DIR}/pickles/acr_word_vocab_embeddings.pickle \
 	--output_label_encoders ${DATA_DIR}/pickles/acr_label_encoders.pickle \
 	--output_tf_records_path "${DATA_DIR}/articles_tfrecords/gcom_articles_tokenized_*.tfrecord.gz" \
 	--articles_by_tfrecord 5000



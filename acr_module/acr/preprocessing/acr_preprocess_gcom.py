import re, sys
import argparse
import pandas as pd
import numpy as np
import ast
import spacy
nlp = spacy.load('en')
from bs4 import BeautifulSoup
from multiprocessing import Pool, Manager
import time
from sklearn.preprocessing import LabelEncoder

import tensorflow as tf

from acr.tf_records_management import export_dataframe_to_tf_records, make_sequential_feature
from acr.utils import serialize
from acr.preprocessing.tokenization import tokenize_articles, nan_to_str, convert_tokens_to_int, get_words_freq
from acr.preprocessing.word_embeddings import load_word_embeddings, process_word_embedding_for_corpus_vocab, save_word_vocab_embeddings


def create_args_parser():
    parser = argparse.ArgumentParser()
    DATA_DIR = '/home/user/Dropbox'
    parser.add_argument(
            '--input_articles_csv_path', default=DATA_DIR+'/Data/input_articles.tsv',
            help='Input path of the news CSV file.')

    parser.add_argument(
            '--input_word_embeddings_path', default=DATA_DIR+'/articles_word2vec/w2v_model',
            help='Input path of the word2vec embeddings model (word2vec).')    

    parser.add_argument(
            '--output_tf_records_path', default=DATA_DIR+'/articles_tfrecords/gcom_articles_tokenized_*.tfrecord.gz',
            help='Output path for generated TFRecords with news content.')

    parser.add_argument(
            '--output_word_vocab_embeddings_path', default=DATA_DIR+'/pickles/acr_word_vocab_embeddings.pickle',
            help='Output path for a pickle with words vocabulary and corresponding word embeddings.')

    parser.add_argument(
            '--output_label_encoders', default=DATA_DIR+'/pickles/acr_label_encoders.pickle',
            help='Output path for a pickle with label encoders (article_id, category_id, publisher_id).')

    parser.add_argument(
        '--articles_by_tfrecord', type=int, default=5000,
        help='Number of articles to be exported in each TFRecords file')

    parser.add_argument(
        '--vocab_most_freq_words', type=int, default=50000,
        help='Most frequent words to keep in vocab')

    return parser


#############################################################################################
#Based on text cleaner used to generate Brazilian Portuguese word embeddings:
#https://github.com/nathanshartmann/portuguese_word_embeddings/blob/master/preprocessing.py

# Punctuation list
punctuations = re.escape('!"#%\'()*+,./:;<=>?@[\\]^_`{|}~')

re_remove_brackets = re.compile(r'\{.*\}')
re_remove_html = re.compile(r'<(\/|\\)?.+?>', re.UNICODE)
re_transform_numbers = re.compile(r'\d', re.UNICODE)
re_transform_emails = re.compile(r'[^\s]+@[^\s]+', re.UNICODE)
re_transform_url = re.compile(r'(http|https)://[^\s]+', re.UNICODE)
# Different quotes are used.
re_quotes_1 = re.compile(r"(?u)(^|\W)[‘’′`']", re.UNICODE)
re_quotes_2 = re.compile(r"(?u)[‘’`′'](\W|$)", re.UNICODE)
re_quotes_3 = re.compile(r'(?u)[‘’`′“”]', re.UNICODE)
re_dots = re.compile(r'(?<!\.)\.\.(?!\.)', re.UNICODE)
re_punctuation = re.compile(r'([,";:]){2},', re.UNICODE)
re_hiphen = re.compile(r' -(?=[^\W\d_])', re.UNICODE)
re_tree_dots = re.compile(u'…', re.UNICODE)
# Differents punctuation patterns are used.
re_punkts = re.compile(r'(\w+)([%s])([ %s])' %
                       (punctuations, punctuations), re.UNICODE)
re_punkts_b = re.compile(r'([ %s])([%s])(\w+)' %
                         (punctuations, punctuations), re.UNICODE)
re_punkts_c = re.compile(r'(\w+)([%s])$' % (punctuations), re.UNICODE)
re_changehyphen = re.compile(u'–')
re_doublequotes_1 = re.compile(r'(\"\")')
re_doublequotes_2 = re.compile(r'(\'\')')
re_trim = re.compile(r' +', re.UNICODE)


def clean_str(string):
    string = string.replace('\n', ' ')
    """Apply all regex above to a given string."""
    string = string.lower()
    string = re_tree_dots.sub('...', string)
    string = re.sub('\.\.\.', '', string)
    string = re_remove_brackets.sub('', string)
    string = re_changehyphen.sub('-', string)
    string = re_remove_html.sub(' ', string)
    string = re_transform_numbers.sub('0', string)
    string = re_transform_url.sub('URL', string)
    string = re_transform_emails.sub('EMAIL', string)
    string = re_quotes_1.sub(r'\1"', string)
    string = re_quotes_2.sub(r'"\1', string)
    string = re_quotes_3.sub('"', string)
    string = re.sub('"', '', string)
    string = re_dots.sub('.', string)
    string = re_punctuation.sub(r'\1', string)
    string = re_hiphen.sub(' - ', string)
    string = re_punkts.sub(r'\1 \2 \3', string)
    string = re_punkts_b.sub(r'\1 \2 \3', string)
    string = re_punkts_c.sub(r'\1 \2', string)
    string = re_doublequotes_1.sub('\"', string)
    string = re_doublequotes_2.sub('\'', string)
    string = re_trim.sub(' ', string)

    return string.strip()

def clean_summary(string):
    
    re_backslash = re.compile(r'\\\\')
    string = re_backslash.sub(r'\\', string)
    
    return string.strip()



def parseSents(args):
    sentences = list()
    doc, q = args

    html_content = doc[0]
    summary = doc[1]
    title = [doc[2]]

    soup = BeautifulSoup(html_content, 'html.parser')
    pis = soup.findAll('p')

    # turn p tags to list of strings
    content = []
    for match in pis:
        text = match.get_text()
        content.append(text)

    # combine all texts to one list of strings for parsing
    content = title + summary + content
    text = '.'.join(content).replace('$','dollar ').replace('%',' percent')
    parsed = nlp(text)
    for sent in parsed.sents:
        current_sen = []
        for tok in sent:
            if (tok.is_stop == False) and (tok.is_punct == False) and (tok.pos_ != 'NUM')  and tok.text != ' ':
                current_sen.append(tok.lemma_.lower())
            elif (tok.pos_ == 'NUM' and tok.ent_type_ == 'DATE'):
                current_sen.append('date')

        sentences.append(current_sen)

    flat_sentences = [item for sublist in sentences for item in sublist]

    return ' '.join(flat_sentences)


def nan_to_list(value):
    return '[]' if type(value) == float else value

def nan_to_cat(value):
    return ',-1,' if type(value) == float else value

def order_str(string):
    
    string = string.split(',')
    string = list(filter(None, string))
    string = sorted(string)
    return ','.join(string)

#############################################################################################

def load_input_csv(path):
    news_df = pd.read_csv(path, encoding = 'utf-8', sep='\t')

    content = news_df['content'].apply(nan_to_str).tolist()
    
    summary = news_df['summary'].apply(nan_to_list)
    summary = summary.apply(clean_summary)
    summary = [ast.literal_eval(x) for x in summary]
    
    news_df['created_at_ts'] = pd.to_datetime(news_df['created_at_ts']).astype(np.int64) // 10 ** 9
    
    # Handle NaN in categories
    news_df['category_id'] = news_df['category_id'].apply(nan_to_cat)
    news_df['publisher_id'] = news_df['publisher_id'].apply(nan_to_cat)
    
    # reduce number of categories
    news_df['category_id'] = news_df['category_id'].apply(order_str)
    news_df['publisher_id'] = news_df['publisher_id'].apply(order_str)
    
    t0 = time.time()
    p = Pool()
    m = Manager()
    q = m.Queue()

    args = [(i, q) for idx, i in enumerate(zip(content, summary, news_df.title.tolist()))]

    result = p.map_async(parseSents, args, chunksize=1)

    while not result.ready():
        remaining = result._number_left * result._chunksize
        t = time.time() - t0
        sys.stderr.write('\rRemaining: {0:} Elapsed: {1:7.3f}'.format(remaining,  t))
        sys.stderr.flush()
        time.sleep(1)

    sentences = result.get()

    print ("\nParesed {} sentences".format(len(sentences)))

    assert len(sentences) == len(news_df.index)
    #Concatenating all available text
    news_df['full_text'] = np.asarray(sentences)
    # news_df['full_text'] = (news_df['title'].apply(nan_to_str) + ". " + \
    #                         news_df['summary'].apply(nan_to_str) + ". " + \
    #                         news_df['content'].apply(nan_to_str)
    #                    ).apply(clean_and_filter_first_sentences)

    return news_df

def process_cat_features(dataframe):
    article_id_encoder = LabelEncoder()
    dataframe['id_encoded'] = article_id_encoder.fit_transform(dataframe['article_id'])

    category_id_encoder = LabelEncoder()
    dataframe['categoryid_encoded'] = category_id_encoder.fit_transform(dataframe['category_id'])

    publisher_id_encoder = LabelEncoder()
    dataframe['publisherid_encoded'] = publisher_id_encoder.fit_transform(dataframe['publisher_id'])

    return article_id_encoder, category_id_encoder, publisher_id_encoder

def save_article_cat_encoders(output_path, article_id_encoder, category_id_encoder, publisher_id_encoder):
    to_serialize = {'article_id': article_id_encoder
                    ,'category_id': category_id_encoder
                    ,'publisher_id': publisher_id_encoder
                    }
    serialize(output_path, to_serialize)


def make_sequence_example(row):
    context_features = {
        'article_id': tf.train.Feature(int64_list=tf.train.Int64List(value=[row['id_encoded']])),
        'category_id': tf.train.Feature(int64_list=tf.train.Int64List(value=[row['categoryid_encoded']])),
        'publisher_id': tf.train.Feature(int64_list=tf.train.Int64List(value=[row['publisherid_encoded']])),
        'created_at_ts': tf.train.Feature(int64_list=tf.train.Int64List(value=[row['created_at_ts']])),
        'text_length': tf.train.Feature(int64_list=tf.train.Int64List(value=[row['text_length']]))
    }

    context = tf.train.Features(feature=context_features)

    sequence_features = {
        'text': make_sequential_feature(row["text_int"], vtype=int)        
    }

    sequence_feature_lists = tf.train.FeatureLists(feature_list=sequence_features)

    return tf.train.SequenceExample(feature_lists=sequence_feature_lists,
                                    context=context
                                   )    

def main():
    parser = create_args_parser()
    args = parser.parse_args()

    print('Loading news article CSV: {}'.format(args.input_articles_csv_path))
    news_df = load_input_csv(args.input_articles_csv_path)

    print('Encoding categorical features')
    article_id_encoder, category_id_encoder, publisher_id_encoder = process_cat_features(news_df)
    print('Exporting LabelEncoders of categorical features: {}'.format(args.output_label_encoders))
    save_article_cat_encoders(args.output_label_encoders
                              ,article_id_encoder
                              ,category_id_encoder
                              ,publisher_id_encoder
                              )

    print('Tokenizing articles...')
    tokenized_articles = tokenize_articles(news_df['full_text'], clean_str)

    print('Computing word frequencies...')
    words_freq = get_words_freq(tokenized_articles)
    print('Corpus vocabulary size: {}'.format(len(words_freq)))

    print("Loading word2vec model and extracting words of this corpus' vocabulary...")
    w2v_model = load_word_embeddings(args.input_word_embeddings_path)
    word_vocab, word_embeddings_matrix = process_word_embedding_for_corpus_vocab(w2v_model, 
                                                                                words_freq,
                                                                                args.vocab_most_freq_words)

    print('Saving word embeddings and vocab.: {}'.format(args.output_word_vocab_embeddings_path))
    save_word_vocab_embeddings(args.output_word_vocab_embeddings_path, 
                               word_vocab, word_embeddings_matrix)

    print('Converting tokens to int numbers (according to the vocab.)...')
    texts_int, texts_lengths = convert_tokens_to_int(tokenized_articles, word_vocab)
    news_df['text_length'] = texts_lengths
    news_df['text_int'] = texts_int

    data_to_export_df = news_df[['id_encoded', 
                                 'categoryid_encoded',
                                 'publisherid_encoded',
                                 'created_at_ts',
                                 'text_length',
                                 'text_int']]
    
    # save records in readable format
    serialize('/home/user/Dropbox/Data/processed_articles.pickle', data_to_export_df)
    
    print('Exporting tokenized articles to TFRecords: {}'.format(args.output_tf_records_path))                                
    export_dataframe_to_tf_records(data_to_export_df, 
                                   make_sequence_example,
                                   output_path=args.output_tf_records_path, 
                                   examples_by_file=args.articles_by_tfrecord)

if __name__ == '__main__':
    main()
